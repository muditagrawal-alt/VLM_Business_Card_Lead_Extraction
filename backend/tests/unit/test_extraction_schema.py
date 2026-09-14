"""The contract handed to the model, and how tolerant we are of its answer.

These two properties pull in opposite directions on purpose: generation is
constrained as tightly as possible, while validation accepts as much as it can.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.models.enums import PhoneType
from app.schemas.extraction import CardExtraction

REQUIRED_LEAD_FIELDS = ("first_name", "last_name", "position", "company")


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return CardExtraction.grammar_schema()  # type: ignore[return-value]


class TestGrammarSchemaShape:
    def test_no_reference_indirection_remains(self, schema: dict[str, Any]) -> None:
        """Hosted providers are inconsistent about $ref, so it is inlined."""
        assert "$defs" not in schema
        assert "$ref" not in json.dumps(schema)

    def test_every_data_field_is_required(self, schema: dict[str, Any]) -> None:
        """An optional property in a grammar is one the model may skip.

        A 4B model was observed emitting raw_text, then company, then jumping
        straight to notes — omitting the name, position, email and phone. Each
        field permits null, so "required" means the model must answer, and an
        explicit null is a valid answer.
        """
        for name in (*REQUIRED_LEAD_FIELDS, "raw_text", "emails", "phones", "address"):
            assert name in schema["required"], name

    def test_notes_stays_optional(self, schema: dict[str, Any]) -> None:
        """Notes is commentary; requiring it invites the model to invent some."""
        assert "notes" not in schema["required"]

    def test_nested_objects_are_required_too(self, schema: dict[str, Any]) -> None:
        """A half-emitted address is the same failure as a half-emitted card."""
        assert schema["properties"]["address"]["required"] == [
            "street",
            "city",
            "state",
            "country",
            "postal_code",
        ]
        assert set(schema["properties"]["phones"]["items"]["required"]) == {
            "number",
            "type",
        }

    def test_defaults_are_stripped(self, schema: dict[str, Any]) -> None:
        """A default signals to a grammar that a property may be left out."""
        assert "default" not in schema["properties"]["company"]
        assert "default" not in schema["properties"]["address"]

    def test_free_text_is_length_bounded(self, schema: dict[str, Any]) -> None:
        """Unbounded free text is where a small model degenerates.

        The notes field was observed looping the same sentences until the token
        budget ran out, losing the fields that mattered.
        """

        def max_len(field: str) -> int | None:
            for branch in schema["properties"][field]["anyOf"]:
                if branch.get("type") == "string":
                    return branch.get("maxLength")
            return None

        assert max_len("notes") == 400
        assert max_len("company") == 256
        assert max_len("first_name") == 128

    def test_every_field_permits_null(self, schema: dict[str, Any]) -> None:
        """Required must never mean "invent a value"."""
        for field in REQUIRED_LEAD_FIELDS:
            types = {b.get("type") for b in schema["properties"][field]["anyOf"]}
            assert "null" in types, field

    def test_raw_text_is_declared_first(self, schema: dict[str, Any]) -> None:
        assert next(iter(schema["properties"])) == "raw_text"


class TestValidationLeniency:
    def test_a_partial_response_is_accepted(self) -> None:
        """A hosted provider returning fewer keys must not lose the card."""
        card = CardExtraction.model_validate({"raw_text": "Acme Corp"})
        assert card.company is None
        assert card.emails == []
        assert card.phones == []
        assert card.address.is_empty()

    def test_unknown_keys_are_ignored(self) -> None:
        card = CardExtraction.model_validate(
            {"raw_text": "x", "company": "Acme", "confidence_score": 0.9}
        )
        assert card.company == "Acme"

    def test_an_unlabelled_phone_defaults_to_unknown(self) -> None:
        card = CardExtraction.model_validate(
            {"raw_text": "x", "phones": [{"number": "+44 20 7946 0958"}]}
        )
        assert card.phones[0].type is PhoneType.UNKNOWN

    def test_an_invalid_phone_label_is_rejected(self) -> None:
        """The enum is the contract; a novel label is a bug worth surfacing."""
        with pytest.raises(ValueError, match="type"):
            CardExtraction.model_validate(
                {"raw_text": "x", "phones": [{"number": "1", "type": "carrier-pigeon"}]}
            )

    def test_overlong_text_is_rejected_by_validation(self) -> None:
        """The same caps the grammar enforces also guard the database columns."""
        with pytest.raises(ValueError, match="company"):
            CardExtraction.model_validate({"raw_text": "x", "company": "A" * 300})
