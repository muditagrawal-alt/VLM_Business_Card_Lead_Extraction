"""Normalisation: turning what the model read into what the sales team can use."""

from __future__ import annotations

import pytest

from app.models.enums import PhoneType
from app.schemas.extraction import CardExtraction, PhoneEntry, PostalAddress
from app.services.normalisation import (
    PhoneValidity,
    build_location,
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
