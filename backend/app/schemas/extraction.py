"""The contract between the vision model and the rest of the system.

These models are the single source of truth in three directions:

1. `CardExtraction.model_json_schema()` is handed to llama.cpp, which compiles
   it into a grammar the model is forced to follow. Malformed JSON and unknown
   keys become impossible rather than merely unlikely.
2. The worker validates every response against it, including responses from
   hosted providers that offer weaker output guarantees.
3. The normalisation layer consumes it to produce the stored lead.

Every field is optional. A business card that prints no job title must yield a
null title; a model that invents a plausible one has produced a worse result
than a blank.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PhoneType

# Excluded from the grammar's required set: notes is commentary, not data, and
# forcing the model to fill it invites it to write something.
_OPTIONAL_IN_GRAMMAR = frozenset({"notes"})


class PhoneEntry(BaseModel):
    """A phone number exactly as printed, with its label if the card gives one."""

    model_config = ConfigDict(extra="ignore")

    number: str = Field(description="The phone number exactly as printed on the card.")
    type: PhoneType = Field(
        default=PhoneType.UNKNOWN,
        description=(
            "The label printed beside the number: mobile, office, fax or other. "
            "Use 'unknown' when the card gives no label."
        ),
    )


class PostalAddress(BaseModel):
    """The address broken into parts, so a display location can be derived."""

    model_config = ConfigDict(extra="ignore")

    street: str | None = Field(default=None, description="Street address including number.")
    city: str | None = Field(default=None, description="City or town.")
    state: str | None = Field(default=None, description="State, province or region.")
    country: str | None = Field(default=None, description="Country.")
    postal_code: str | None = Field(default=None, description="Postal or ZIP code.")

    def is_empty(self) -> bool:
        return not any((self.street, self.city, self.state, self.country, self.postal_code))


class CardExtraction(BaseModel):
    """Everything readable from a single business card."""

    # Unknown keys are dropped rather than rejected: a hosted provider that
    # decorates its response should not fail an otherwise usable card.
    model_config = ConfigDict(extra="ignore")

    # Declared first deliberately. Grammar-constrained decoding emits
    # properties in schema order, so transcribing the card before filling the
    # structured fields lets the model read once and then extract from its own
    # transcription, rather than extracting and transcribing independently.
    # It is required rather than defaulted so the grammar cannot skip it.
    raw_text: str = Field(
        description=(
            "Every line of text visible on the card, transcribed verbatim, separated by newlines."
        )
    )

    first_name: str | None = Field(
        default=None,
        max_length=128,
        description="Given name only, without any honorific or title.",
    )
    last_name: str | None = Field(
        default=None,
        max_length=128,
        description="Family name only, without any suffix such as Jr or PhD.",
    )
    full_name_as_printed: str | None = Field(
        default=None,
        max_length=256,
        description="The person's name exactly as it appears, including any honorific.",
    )
    position: str | None = Field(
        default=None,
        max_length=256,
        description="Job title or role, for example 'Head of Sales'.",
    )
    company: str | None = Field(
        default=None, max_length=256, description="Organisation or company name."
    )

    emails: list[str] = Field(
        default_factory=list, description="Every email address printed on the card."
    )
    phones: list[PhoneEntry] = Field(
        default_factory=list, description="Every phone number printed on the card."
    )
    website: str | None = Field(
        default=None,
        max_length=512,
        description="Website or domain, excluding social media handles.",
    )
    address: PostalAddress = Field(
        default_factory=PostalAddress, description="The postal address, split into parts."
    )

    # Capped tightly and declared last. Left unbounded and free-form, a small
    # model degenerates into a repetition loop here, burning the whole token
    # budget and losing the fields that actually matter.
    notes: str | None = Field(
        default=None,
        max_length=400,
        description=(
            "Anything a reviewer should know: a second person on the card, "
            "unreadable text, or an honorific and suffix that were removed "
            "from the name fields."
        ),
    )

    @classmethod
    def grammar_schema(cls) -> dict[str, object]:
        """JSON schema for constrained decoding.

        This diverges from the validation schema in two deliberate ways.

        First, `$defs`/`$ref` indirection is inlined. llama.cpp handles
        references, but hosted providers are inconsistent about them, so one
        flattened payload works everywhere.

        Second, every field except `notes` is marked required. Pydantic makes a
        defaulted field optional, and an optional property in a grammar is one
        the model may simply skip — observed in practice, with a 4B model
        emitting `raw_text`, then `company`, then jumping straight to `notes`
        and omitting the name, position, email and phone entirely. Because each
        field also permits null, "required" here means the model must *answer*
        for every field, and an explicit null is a valid answer. The defaults
        are stripped for the same reason: a default is a signal that the
        property can be left out.

        Validation stays lenient (see the field defaults above), so a hosted
        provider returning a partial object is still accepted rather than
        discarded.
        """
        schema = cls.model_json_schema()
        defs = schema.pop("$defs", {})

        def inline(node: object) -> object:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    target = defs.get(ref.removeprefix("#/$defs/"), {})
                    merged = {**inline(target), **{k: v for k, v in node.items() if k != "$ref"}}  # type: ignore[dict-item]
                    return merged
                return {k: inline(v) for k, v in node.items()}
            if isinstance(node, list):
                return [inline(item) for item in node]
            return node

        resolved = inline(schema)
        if isinstance(resolved, dict):
            _require_all_properties(resolved, _OPTIONAL_IN_GRAMMAR)
        return resolved  # type: ignore[return-value]


def _require_all_properties(node: dict[str, object], optional: frozenset[str]) -> None:
    """Mark every property of every object in the schema as required.

    Applied recursively so nested objects (the postal address, each phone
    entry) are covered too: a partially emitted address is the same failure as
    a partially emitted card.
    """
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = [name for name in properties if name not in optional]
        node["additionalProperties"] = False
        for child in properties.values():
            if isinstance(child, dict):
                child.pop("default", None)
                _require_all_properties(child, optional)

    # Recurse through the containers a JSON schema can nest objects in.
    for key in ("items", "prefixItems"):
        child = node.get(key)
        if isinstance(child, dict):
            _require_all_properties(child, optional)
    for key in ("anyOf", "oneOf", "allOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    _require_all_properties(branch, optional)
