"""Normalisation: turning what the model read into what the sales team can use."""

from __future__ import annotations

import pytest

from app.models.enums import PhoneType
from app.schemas.extraction import CardExtraction, PhoneEntry, PostalAddress
from app.services.normalisation import (
    PhoneValidity,
    build_location,
    infer_region,
    normalise,
    normalise_email,
    normalise_phone,
    normalise_website,
    parse_phone,
    region_for_country,
    split_name,
)


class TestPhoneParsing:
    @pytest.mark.parametrize(
        ("raw", "region", "expected"),
        [
            ("+91 98765 43210", None, "+919876543210"),
            ("98765 43210", "IN", "+919876543210"),
            ("+1 (415) 555-0142", None, "+14155550142"),
            ("(415) 555-0142", "US", "+14155550142"),
            ("+44 20 7946 0958", None, "+442079460958"),
            ("+46 70 123 45 67", None, "+46701234567"),
        ],
    )
    def test_valid_numbers_become_e164(self, raw: str, region: str | None, expected: str) -> None:
        assert normalise_phone(raw, region=region) == expected

    def test_possible_but_unconfirmed_numbers_are_kept(self) -> None:
        """libphonenumber's metadata lags real allocations.

        This Dubai landline fails is_valid_number but passes
        is_possible_number. Discarding it would lose a prospect's real phone
        number, so it is kept and marked for review instead.
        """
        parsed = parse_phone("+971 4 123 4567")
        assert parsed is not None
        assert parsed.value == "+97141234567"
        assert parsed.validity is PhoneValidity.POSSIBLE

    def test_unparseable_text_is_preserved_verbatim(self) -> None:
        parsed = parse_phone("ext. 4402")
        assert parsed is not None
        assert parsed.value == "ext. 4402"
        assert parsed.validity is PhoneValidity.UNPARSED

    def test_unparseable_text_is_not_offered_as_a_number(self) -> None:
        # The strict helper is what the primary phone column relies on.
        assert normalise_phone("ext. 4402") is None
        assert normalise_phone("not a phone") is None

    @pytest.mark.parametrize("raw", [None, "", "   ", "n/a", "unknown", "-"])
    def test_empty_placeholders_yield_nothing(self, raw: str | None) -> None:
        assert parse_phone(raw) is None


class TestPhoneSelection:
    def _card(self, phones: list[PhoneEntry], raw_text: str = "") -> CardExtraction:
        return CardExtraction(
            raw_text=raw_text,
            phones=phones,
            address=PostalAddress(city="Lagos", country="Nigeria"),
        )

    def test_mobile_is_preferred_over_office_and_fax(self) -> None:
        lead = normalise(
            self._card(
                [
                    PhoneEntry(number="+234 1 271 0000", type=PhoneType.FAX),
                    PhoneEntry(number="+44 20 7946 0958", type=PhoneType.OFFICE),
                    PhoneEntry(number="+234 803 555 0188", type=PhoneType.MOBILE),
                ]
            )
        )
        assert lead.phone == "+2348035550188"

    def test_fax_is_never_chosen_when_another_number_exists(self) -> None:
        lead = normalise(
            self._card(
                [
                    PhoneEntry(number="+44 20 7946 0958", type=PhoneType.FAX),
                    PhoneEntry(number="+234 803 555 0188", type=PhoneType.UNKNOWN),
                ]
            )
        )
        assert lead.phone == "+2348035550188"

    def test_recognised_number_beats_an_unparseable_mobile(self) -> None:
        """Label priority must not promote a number nobody can dial."""
        lead = normalise(
            self._card(
                [
                    PhoneEntry(number="mobile on request", type=PhoneType.MOBILE),
                    PhoneEntry(number="+44 20 7946 0958", type=PhoneType.OFFICE),
                ]
            )
        )
        assert lead.phone == "+442079460958"

    def test_every_printed_number_survives_as_an_extra(self) -> None:
        lead = normalise(
            self._card(
                [
                    PhoneEntry(number="+234 803 555 0188", type=PhoneType.MOBILE),
                    PhoneEntry(number="+234 1 270 4411", type=PhoneType.OFFICE),
                    PhoneEntry(number="+234 1 270 4412", type=PhoneType.FAX),
                ]
            )
        )
        assert lead.phone == "+2348035550188"
        assert [e["number"] for e in lead.extra_phones] == [
            "+234 1 270 4411",
            "+234 1 270 4412",
        ]

    def test_duplicate_numbers_are_collapsed(self) -> None:
        lead = normalise(
            self._card(
                [
                    PhoneEntry(number="+44 20 7946 0958", type=PhoneType.OFFICE),
                    PhoneEntry(number="+442079460958", type=PhoneType.MOBILE),
                ]
            )
        )
        assert lead.phone == "+442079460958"
        assert lead.extra_phones == []

    def test_unconfirmed_primary_number_is_flagged_for_review(self) -> None:
        lead = normalise(
            CardExtraction(
                raw_text="+971 4 123 4567",
                phones=[PhoneEntry(number="+971 4 123 4567", type=PhoneType.OFFICE)],
                address=PostalAddress(city="Dubai", country="United Arab Emirates"),
            )
        )
        assert lead.phone == "+97141234567"
        # Below 1.0 so the table and workbook mark it amber.
        assert lead.confidence["phone"] == pytest.approx(0.7)


class TestEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Priya.Raghavan@MeridianLog.com", "priya.raghavan@meridianlog.com"),
            ("email: a@b.co", "a@b.co"),
            ("<x@example.com>", "x@example.com"),
        ],
    )
    def test_valid_addresses_are_normalised(self, raw: str, expected: str) -> None:
        assert normalise_email(raw) == expected

    @pytest.mark.parametrize("raw", ["nonsense@@x", "x@y", "no-at-sign", None, ""])
    def test_invalid_addresses_are_rejected(self, raw: str | None) -> None:
        assert normalise_email(raw) is None


class TestWebsite:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://Meridianlog.com/", "meridianlog.com"),
            ("http://example.org", "example.org"),
            ("WWW.Example.COM", "www.example.com"),
        ],
    )
    def test_scheme_and_trailing_slash_are_removed(self, raw: str, expected: str) -> None:
        assert normalise_website(raw) == expected


class TestNames:
    def test_model_split_is_trusted_when_complete(self) -> None:
        assert split_name("Priya", "Raghavan", None) == ("Priya", "Raghavan")

    def test_honorifics_and_suffixes_are_removed(self) -> None:
        assert split_name("Dr. Eleanor", "Whitfield, PhD", "Dr. Eleanor Whitfield, PhD") == (
            "Eleanor",
            "Whitfield",
        )

    def test_printed_name_is_parsed_when_parts_are_missing(self) -> None:
        assert split_name(None, None, "Hiroshi Tanaka") == ("Hiroshi", "Tanaka")

    def test_an_initial_is_never_expanded(self) -> None:
        """Expanding 'J.' into a guessed given name would fabricate data."""
        assert split_name("J.", "Wehrmann", None) == ("J.", "Wehrmann")

    def test_no_name_yields_nothing(self) -> None:
        assert split_name(None, None, None) == (None, None)


class TestCompany:
    def test_trademark_symbols_are_stripped(self) -> None:
        """A wordmark's ® is part of the logo, not part of the name."""
        lead = normalise(
            CardExtraction(raw_text="BAJAJCAPITAL® Anil Chopra", company="BAJAJCAPITAL®"),
        )
        assert lead.company == "BAJAJCAPITAL"
        # The transcription still grounds the cleaned value.
        assert lead.confidence["company"] == 1.0

    def test_a_company_that_is_only_a_mark_becomes_null(self) -> None:
        lead = normalise(CardExtraction(raw_text="™", company="™"))
        assert lead.company is None


class TestLocation:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ({"city": "Mumbai", "country": "India"}, "Mumbai, India"),
            (
                {"city": "San Francisco", "state": "CA", "country": "USA"},
                "San Francisco, CA, USA",
            ),
            ({"city": "Tokyo"}, "Tokyo"),
            ({"country": "Germany"}, "Germany"),
            ({}, None),
            ({"city": None, "state": None, "country": None}, None),
        ],
    )
    def test_location_uses_the_parts_available(
        self, address: dict[str, str | None], expected: str | None
    ) -> None:
        assert build_location(address) == expected


class TestCountryRegion:
    @pytest.mark.parametrize(
        ("country", "region"),
        [
            ("India", "IN"),
            ("united arab emirates", "AE"),
            ("USA", "US"),
            ("U.K.", "GB"),
            ("Atlantis", None),
            (None, None),
        ],
    )
    def test_known_countries_map_to_regions(self, country: str | None, region: str | None) -> None:
        assert region_for_country(country) == region


class TestConfidence:
    def test_values_absent_from_the_transcription_score_lower(self) -> None:
        """A field the model invented usually fails the grounding check."""
        grounded = normalise(
            CardExtraction(raw_text="Acme Corp\nHead of Sales", company="Acme Corp")
        )
        invented = normalise(
            CardExtraction(raw_text="Acme Corp\nHead of Sales", company="Globex Inc")
        )
        assert grounded.confidence["company"] == 1.0
        assert invented.confidence["company"] < 1.0

    def test_missing_fields_score_zero(self) -> None:
        lead = normalise(CardExtraction(raw_text="nothing useful"))
        assert lead.confidence["email"] == 0.0
        assert lead.confidence["phone"] == 0.0


class TestPlaceholderRejection:
    @pytest.mark.parametrize("placeholder", ["null", "None", "N/A", "not provided", "--"])
    def test_model_placeholders_do_not_become_values(self, placeholder: str) -> None:
        """Small models sometimes write a placeholder instead of omitting."""
        lead = normalise(CardExtraction(raw_text="x", company=placeholder))
        assert lead.company is None


class TestRegionInference:
    """Working out the card's country without a printed country name.

    These cases come from real cards: Indian business cards routinely print a
    bare ten-digit mobile and never name the country.
    """

    def test_no_country_is_ever_invented(self) -> None:
        """A bare national number must not acquire a guessed country code.

        An earlier version fell back to "US", turning the Indian mobile
        7055559999 into +17055559999 and reporting it VALID — a real North
        American number, undialable for this contact, presented with full
        confidence. A plausible wrong number is worse than none.
        """
        parsed = parse_phone("7055559999", region=None)
        assert parsed is not None
        assert parsed.value == "7055559999"
        assert parsed.validity is PhoneValidity.UNPARSED

    def test_region_comes_from_a_sibling_international_number(self) -> None:
        """One number in full international form resolves all the others."""
        card = CardExtraction(
            raw_text="+91 7055559999, 9013933333",
            phones=[
                PhoneEntry(number="+91 7055559999", type=PhoneType.MOBILE),
                PhoneEntry(number="9013933333", type=PhoneType.OFFICE),
            ],
        )
        assert infer_region(card, None) == "IN"
        lead = normalise(card)
        assert lead.phone == "+917055559999"
        assert [e["number"] for e in lead.extra_phones] == ["+919013933333"]

    def test_region_comes_from_a_country_code_domain(self) -> None:
        card = CardExtraction(
            raw_text="rohit@sinrachna.in 9013933333",
            emails=["rohit@sinrachna.in"],
            phones=[PhoneEntry(number="9013933333", type=PhoneType.MOBILE)],
        )
        assert infer_region(card, None) == "IN"
        assert normalise(card).phone == "+919013933333"

    def test_a_generic_domain_implies_no_country(self) -> None:
        """.com says nothing about where a company is."""
        card = CardExtraction(raw_text="x", emails=["someone@example.com"])
        assert infer_region(card, None) is None

    def test_a_printed_country_wins_over_other_signals(self) -> None:
        card = CardExtraction(
            raw_text="x",
            emails=["someone@example.in"],
            address=PostalAddress(country="Germany"),
        )
        assert infer_region(card, "Germany") == "DE"


class TestGrounding:
    def test_a_value_printed_across_two_lines_is_still_grounded(self) -> None:
        """Cards break long company names over two lines.

        Comparing without collapsing whitespace flagged correctly extracted
        companies as doubtful, which is how this was found.
        """
        lead = normalise(
            CardExtraction(
                raw_text='CORATIA\nTECHNOLOGIES\n"Underwater Robotic Inspection"',
                company="CORATIA TECHNOLOGIES",
            )
        )
        assert lead.confidence["company"] == 1.0

    def test_location_is_scored_by_its_parts(self) -> None:
        """Location is assembled, so it is never printed in its stored form.

        A card shows "New Delhi - 110016", never "New Delhi, India". Scoring
        the joined string flagged every correct location as doubtful.
        """
        lead = normalise(
            CardExtraction(
                raw_text="R&I Park, IIT Delhi, Hauz Khas, New Delhi - 110016",
                address=PostalAddress(city="New Delhi", country="India", postal_code="110016"),
            )
        )
        assert lead.location == "New Delhi, India"
        # City printed, country inferred: normal, and not worth flagging.
        assert lead.confidence["location"] == pytest.approx(0.9)

    def test_location_with_every_part_printed_scores_full(self) -> None:
        lead = normalise(
            CardExtraction(
                raw_text="Mumbai, India",
                address=PostalAddress(city="Mumbai", country="India"),
            )
        )
        assert lead.confidence["location"] == 1.0

    def test_a_location_absent_from_the_card_is_still_flagged(self) -> None:
        """The check must keep catching an invented place."""
        lead = normalise(
            CardExtraction(
                raw_text="no place names on this card",
                address=PostalAddress(city="Atlantis", country="Nowhere"),
            )
        )
        assert lead.confidence["location"] < 0.8


class TestTruncatedNumbers:
    """A partial read must not outrank the number actually printed.

    From a real card photographed beside a second copy of itself with one edge
    cut off: the model transcribed both "+066 54412 7685" and the fragment
    "+4412 7685". Both appear in the transcription, so grounding cannot
    separate them — and the fragment parses as a valid UK number while the
    real one does not, so ranking by recognisability promoted the fragment and
    hid the printed number.
    """

    def test_a_fragment_does_not_become_the_primary_number(self) -> None:
        lead = normalise(
            CardExtraction(
                raw_text="+4412 7685 +066 54412 7685 +066 10923 1853",
                phones=[
                    PhoneEntry(number="+4412 7685"),
                    PhoneEntry(number="+066 54412 7685"),
                    PhoneEntry(number="+066 10923 1853"),
                ],
            )
        )
        assert lead.phone == "+066 54412 7685"
        assert "+44127685" not in str(lead.phone)

    def test_the_other_printed_numbers_survive(self) -> None:
        lead = normalise(
            CardExtraction(
                raw_text="+4412 7685 +066 54412 7685 +066 10923 1853",
                phones=[
                    PhoneEntry(number="+4412 7685"),
                    PhoneEntry(number="+066 54412 7685"),
                    PhoneEntry(number="+066 10923 1853"),
                ],
            )
        )
        assert [e["number"] for e in lead.extra_phones] == ["+066 10923 1853"]

    def test_distinct_numbers_are_all_kept(self) -> None:
        """Only a genuine substring is dropped, never a different number."""
        lead = normalise(
            CardExtraction(
                raw_text="x",
                phones=[
                    PhoneEntry(number="+44 20 7946 0958", type=PhoneType.OFFICE),
                    PhoneEntry(number="+44 20 7946 0959", type=PhoneType.FAX),
                ],
            )
        )
        assert lead.phone == "+442079460958"
        assert len(lead.extra_phones) == 1

    def test_a_single_number_is_never_dropped(self) -> None:
        lead = normalise(CardExtraction(raw_text="x", phones=[PhoneEntry(number="+4412 7685")]))
        assert lead.phone is not None


class TestMislabelledPostalCode:
    def test_a_numeric_state_is_treated_as_a_postal_code(self) -> None:
        """Cards print "NY 1600" together and models split it wrongly.

        Left alone the display location became "NY, 1600", which reads to a
        user as a malfunction. No administrative region is digits alone.
        """
        lead = normalise(
            CardExtraction(
                raw_text="545 Greenview Street NY 1600",
                address=PostalAddress(street="545 Greenview Street", city="NY", state="1600"),
            )
        )
        assert lead.location == "NY"
        assert lead.address["postal_code"] == "1600"
        assert lead.address["state"] is None

    def test_a_real_state_is_left_alone(self) -> None:
        lead = normalise(
            CardExtraction(
                raw_text="San Francisco, CA, USA",
                address=PostalAddress(city="San Francisco", state="CA", country="USA"),
            )
        )
        assert lead.location == "San Francisco, CA, USA"
