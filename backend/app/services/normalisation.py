"""Turn a model's reading of a card into a usable lead.

The model reports what is printed. This layer decides what is *true*: a phone
number becomes dialable, an email is validated rather than assumed, a name is
stripped of the honorifics the model was told to set aside, and one primary
phone and email are chosen from however many the card listed.

Every function here is pure and synchronous so it can be tested exhaustively
without a model, a database or a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from nameparser import HumanName

from app.models.enums import PhoneType

if TYPE_CHECKING:
    from app.schemas.extraction import CardExtraction, PhoneEntry

# Region hints for numbers printed without an international prefix. Only the
# countries likely to appear on a card need to be here; anything else falls
# back to parsing the number as-is, which succeeds whenever a '+' is present.
_COUNTRY_TO_REGION: dict[str, str] = {
    "india": "IN",
    "bharat": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.a.": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "u.k.": "GB",
    "england": "GB",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "switzerland": "CH",
    "austria": "AT",
    "belgium": "BE",
    "ireland": "IE",
    "portugal": "PT",
    "japan": "JP",
    "china": "CN",
    "singapore": "SG",
    "hong kong": "HK",
    "south korea": "KR",
    "korea": "KR",
    "australia": "AU",
    "new zealand": "NZ",
    "canada": "CA",
    "brazil": "BR",
    "mexico": "MX",
    "argentina": "AR",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "egypt": "EG",
    "united arab emirates": "AE",
    "uae": "AE",
    "u.a.e.": "AE",
    "saudi arabia": "SA",
    "qatar": "QA",
    "israel": "IL",
    "turkey": "TR",
    "indonesia": "ID",
    "malaysia": "MY",
    "thailand": "TH",
    "vietnam": "VN",
    "philippines": "PH",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
}

# Titles and suffixes the model is told to move into notes, removed here as a
# second line of defence when it leaves them in the name fields.
_HONORIFICS = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "mx",
    "dr",
    "prof",
    "professor",
    "sir",
    "madam",
    "rev",
    "hon",
    "eng",
    "ir",
}
_SUFFIXES = {
    "jr",
    "sr",
    "ii",
    "iii",
    "iv",
    "phd",
    "ph.d",
    "md",
    "mba",
    "msc",
    "ma",
    "bsc",
    "ba",
    "cpa",
    "esq",
    "pe",
    "cfa",
    "pmp",
}

# Ranking used to pick the number a salesperson should actually call.
_PHONE_PRIORITY: dict[PhoneType, int] = {
    PhoneType.MOBILE: 0,
    PhoneType.OFFICE: 1,
    PhoneType.OTHER: 2,
    PhoneType.UNKNOWN: 3,
    PhoneType.FAX: 4,
}


class PhoneValidity(StrEnum):
    """How much libphonenumber could confirm about a number.

    The distinction matters because libphonenumber's metadata lags telecom
    allocations: real, dialable numbers routinely fail `is_valid_number` while
    passing `is_possible_number`. Discarding those would mean losing a
    prospect's phone number, so they are kept and marked instead.
    """

    VALID = "valid"
    POSSIBLE = "possible"
    UNPARSED = "unparsed"


@dataclass(frozen=True, slots=True)
class ParsedPhone:
    """A phone number with the best form and confidence available."""

    value: str
    validity: PhoneValidity


@dataclass(slots=True)
class NormalisedLead:
    """Lead fields ready to persist, with a per-field confidence score."""

    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    company: str | None = None
    location: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    address: dict[str, str | None] = field(default_factory=dict)
    extra_phones: list[dict[str, str]] = field(default_factory=list)
    extra_emails: list[str] = field(default_factory=list)
    raw_text: str | None = None
    notes: str | None = None
    confidence: dict[str, float] = field(default_factory=dict)


def _clean(value: str | None) -> str | None:
    """Collapse whitespace and drop values that carry no information."""
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    # Models occasionally emit these instead of omitting the field.
    if text.lower() in {"null", "none", "n/a", "na", "-", "--", "unknown", "not provided"}:
        return None
    return text


def _strip_affixes(name: str | None) -> str | None:
    """Remove a leading honorific or trailing suffix from a name part."""
    cleaned = _clean(name)
    if cleaned is None:
        return None
    parts = cleaned.replace(",", " ").split()
    kept = [
        part
        for part in parts
        if part.lower().strip(".") not in _HONORIFICS and part.lower().strip(".") not in _SUFFIXES
    ]
    return " ".join(kept) or None


def region_for_country(country: str | None) -> str | None:
    """Map a printed country name to an ISO region for phone parsing."""
    cleaned = _clean(country)
    if cleaned is None:
        return None
    return _COUNTRY_TO_REGION.get(cleaned.lower())


def parse_phone(raw: str | None, *, region: str | None = None) -> ParsedPhone | None:
    """Parse a printed number into E.164 where possible.

    Tries the country hint from the card's address first, then the number on
    its own (which works whenever a '+' prefix is present), then a US region as
    a last resort for bare local numbers.

    A number that parses but only satisfies `is_possible_number` is still
    returned, marked POSSIBLE. One that cannot be parsed at all is returned
    verbatim and marked UNPARSED, so nothing printed on the card is thrown
    away; the caller decides what is fit to be the primary phone.
    """
    cleaned = _clean(raw)
    if cleaned is None:
        return None

    best: ParsedPhone | None = None
    for candidate_region in (region, None, "US"):
        try:
            parsed = phonenumbers.parse(cleaned, candidate_region)
        except phonenumbers.NumberParseException:
            continue
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        if phonenumbers.is_valid_number(parsed):
            return ParsedPhone(e164, PhoneValidity.VALID)
        if best is None and phonenumbers.is_possible_number(parsed):
            best = ParsedPhone(e164, PhoneValidity.POSSIBLE)

    return best or ParsedPhone(cleaned, PhoneValidity.UNPARSED)


def normalise_phone(raw: str | None, *, region: str | None = None) -> str | None:
    """E.164 form for a number libphonenumber recognises, else None."""
    parsed = parse_phone(raw, region=region)
    if parsed is None or parsed.validity is PhoneValidity.UNPARSED:
        return None
    return parsed.value


def normalise_email(raw: str | None) -> str | None:
    """Lowercase and validate an address, without resolving DNS."""
    cleaned = _clean(raw)
    if cleaned is None:
        return None
    # Cards sometimes print a label alongside the address.
    candidate = cleaned.split()[-1].strip("<>(),;")
    try:
        # Deliverability is a network call and would make extraction flaky.
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


def normalise_website(raw: str | None) -> str | None:
    cleaned = _clean(raw)
    if cleaned is None:
        return None
    return cleaned.removeprefix("https://").removeprefix("http://").removesuffix("/").lower()


def split_name(
    first: str | None, last: str | None, printed: str | None
) -> tuple[str | None, str | None]:
    """Resolve first and last name, falling back to parsing the printed form.

    The model sees layout cues that a text parser cannot, so its split is
    trusted when it supplies both parts.
    """
    first_clean = _strip_affixes(first)
    last_clean = _strip_affixes(last)
    if first_clean and last_clean:
        return first_clean, last_clean

    printed_clean = _strip_affixes(printed)
    if printed_clean and not (first_clean and last_clean):
        parsed = HumanName(printed_clean)
        parsed_first = _clean(parsed.first) or first_clean
        parsed_last = _clean(parsed.last) or last_clean
        if parsed_first or parsed_last:
            return parsed_first, parsed_last
    return first_clean, last_clean


def build_location(address: dict[str, str | None]) -> str | None:
    """Compose the display location from the most specific parts available."""
    parts = [
        _clean(address.get("city")),
        _clean(address.get("state")),
        _clean(address.get("country")),
    ]
    present = [p for p in parts if p]
    return ", ".join(present) if present else None


# Recognised numbers outrank unparseable ones regardless of their label, so an
# unreadable mobile never displaces a working office number as the primary.
_VALIDITY_PRIORITY: dict[PhoneValidity, int] = {
    PhoneValidity.VALID: 0,
    PhoneValidity.POSSIBLE: 1,
    PhoneValidity.UNPARSED: 2,
}


def _rank_phones(
    phones: list[PhoneEntry], region: str | None
) -> list[tuple[ParsedPhone, PhoneType]]:
    """Every printed number in calling priority, deduplicated."""
    ranked: list[tuple[int, int, int, ParsedPhone, PhoneType]] = []
    for index, entry in enumerate(phones):
        parsed = parse_phone(entry.number, region=region)
        if parsed is None:
            continue
        ranked.append(
            (
                _VALIDITY_PRIORITY[parsed.validity],
                _PHONE_PRIORITY.get(entry.type, 3),
                index,  # breaks ties so the card's own ordering is preserved
                parsed,
                entry.type,
            )
        )
    ranked.sort(key=lambda row: row[:3])

    seen: set[str] = set()
    result: list[tuple[ParsedPhone, PhoneType]] = []
    for *_, parsed, phone_type in ranked:
        if parsed.value in seen:
            continue
        seen.add(parsed.value)
        result.append((parsed, phone_type))
    return result


def normalise(extraction: CardExtraction) -> NormalisedLead:
    """Convert a validated model response into a persistable lead."""
    address_raw = extraction.address
    address = {
        "street": _clean(address_raw.street),
        "city": _clean(address_raw.city),
        "state": _clean(address_raw.state),
        "country": _clean(address_raw.country),
        "postal_code": _clean(address_raw.postal_code),
    }
    region = region_for_country(address["country"])

    first_name, last_name = split_name(
        extraction.first_name, extraction.last_name, extraction.full_name_as_printed
    )

    phones = _rank_phones(extraction.phones, region)
    primary_phone = phones[0] if phones else None
    emails = [e for e in (normalise_email(raw) for raw in extraction.emails) if e]
    # Preserve card order while removing duplicates.
    emails = list(dict.fromkeys(emails))

    lead = NormalisedLead(
        first_name=first_name,
        last_name=last_name,
        position=_clean(extraction.position),
        company=_clean(extraction.company),
        location=build_location(address),
        phone=primary_phone[0].value if primary_phone else None,
        email=emails[0] if emails else None,
        website=normalise_website(extraction.website),
        address=address,
        extra_phones=[
            {"number": p.value, "type": t.value, "validity": p.validity.value}
            for p, t in phones[1:]
        ],
        extra_emails=emails[1:],
        raw_text=_clean(extraction.raw_text),
        notes=_clean(extraction.notes),
    )
    lead.confidence = score_confidence(lead, extraction)
    if primary_phone is not None and primary_phone[0].validity is not PhoneValidity.VALID:
        # Cap the score so a number libphonenumber could not confirm is
        # flagged for review even when it matches the transcription exactly.
        ceiling = 0.7 if primary_phone[0].validity is PhoneValidity.POSSIBLE else 0.4
        lead.confidence["phone"] = min(lead.confidence.get("phone", 0.0), ceiling)
    return lead


def score_confidence(lead: NormalisedLead, extraction: CardExtraction) -> dict[str, float]:
    """Heuristic per-field confidence driving the amber flags in the UI.

    This is not a model probability. It answers a narrower, more useful
    question: did the value survive validation, and does it appear in the text
    the model transcribed? A value the model invented usually fails the second
    test.
    """
    haystack = (extraction.raw_text or "").casefold()
    scores: dict[str, float] = {}

    def grounded(value: str | None, *, validated: bool = False) -> float:
        if value is None:
            return 0.0
        # A validated phone or email is trustworthy even if reformatting means
        # it no longer matches the transcription character for character.
        base = 0.9 if validated else 0.6
        if haystack and value.casefold() in haystack:
            return 1.0
        return base

    scores["first_name"] = grounded(lead.first_name)
    scores["last_name"] = grounded(lead.last_name)
    scores["position"] = grounded(lead.position)
    scores["company"] = grounded(lead.company)
    scores["location"] = grounded(lead.location)
    scores["email"] = grounded(lead.email, validated=lead.email is not None)
    scores["phone"] = grounded(lead.phone, validated=lead.phone is not None)

    # A digits-only comparison catches the common case where the stored E.164
    # form differs from the printed grouping.
    if lead.phone:
        digits = "".join(c for c in lead.phone if c.isdigit())
        haystack_digits = "".join(c for c in haystack if c.isdigit())
        if digits and digits[-9:] in haystack_digits:
            scores["phone"] = 1.0
    return scores
