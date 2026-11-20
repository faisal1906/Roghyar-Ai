"""Private single-message routing. No clinical or public HTTP integration."""

import asyncio
import json
import math
import unicodedata
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from .assessment import normalize_text

Language = Literal["en", "ur", "ps"]
Category = Literal[
    "symptom", "disease_info", "medicine", "doctor_or_hospital", "booking_or_app_help",
    "wellness", "greeting", "off_topic", "unsafe_request",
]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
MAX_TEXT = 4000
MAX_OUTPUT_BYTES = 4096
CLARIFY_BELOW = 0.60
CATEGORY_ORDER: tuple[Category, ...] = (
    "unsafe_request", "symptom", "disease_info", "medicine", "doctor_or_hospital",
    "booking_or_app_help", "wellness", "greeting", "off_topic",
)


class StrictContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True, frozen=True)


class ClassificationInput(StrictContract):
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)] = Field(repr=False)
    language_hint: Language | None = None

    @field_validator("text")
    @classmethod
    def normalized_text(cls, value: str) -> str:
        for char in value:
            if unicodedata.category(char).startswith("C") and char not in "\n\r\t\u200c\u200d":
                raise ValueError("Unsupported character")
        normalized = normalize_text(value)
        if len(normalized) > MAX_TEXT:
            raise ValueError("Normalized message is too long")
        return normalized


class ProviderOutput(StrictContract):
    languages: Annotated[tuple[Language, ...], Field(max_length=3)]
    relevance_categories: Annotated[tuple[Category, ...], Field(max_length=9)]
    relevance_confidence: Score | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        for values in (self.languages, self.relevance_categories):
            if len(set(values)) != len(values):
                raise ValueError("Duplicate labels")
        if bool(self.relevance_categories) != (self.relevance_confidence is not None):
            raise ValueError("Category and score must be supplied together")
        return self


class ResultBase(StrictContract):
    classifier_version: Literal["1"] = "1"


class Classified(ResultBase):
    status: Literal["classified"] = "classified"
    languages: Annotated[tuple[Language, ...], Field(min_length=1, max_length=3)]
    relevance_category: Category
    relevance_confidence: Annotated[float, Field(ge=CLARIFY_BELOW, le=1, allow_inf_nan=False)]
    needs_clarification: Literal[False] = False
    reason_code: Literal["classified"] = "classified"


class Uncertain(ResultBase):
    status: Literal["uncertain"] = "uncertain"
    languages: Annotated[tuple[Language, ...], Field(max_length=3)]
    needs_clarification: Literal[True] = True
    reason_code: Literal["language_unknown", "relevance_uncertain"]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.reason_code == "language_unknown") != (not self.languages):
            raise ValueError("Language status is inconsistent")
        return self


class Rejected(ResultBase):
    status: Literal["rejected"] = "rejected"
    reason_code: Literal["invalid_input"] = "invalid_input"


class Unavailable(ResultBase):
    status: Literal["unavailable"] = "unavailable"
    reason_code: Literal[
        "provider_unconfigured", "provider_unavailable", "provider_timeout", "invalid_provider_json",
        "invalid_provider_schema", "provider_output_too_large", "unexpected_failure",
    ]


ClassificationResult = Annotated[Classified | Uncertain | Rejected | Unavailable, Field(discriminator="status")]


class ProviderUnavailable(Exception):
    pass


class ProviderOutputTooLarge(Exception):
    pass


class InferenceAdapter(Protocol):
    async def infer(self, message: ClassificationInput) -> bytes: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise ValueError("Non-finite JSON number")


def validate_provider_output(raw: bytes) -> ProviderOutput | Unavailable:
    if type(raw) is not bytes:
        return Unavailable(reason_code="invalid_provider_schema")
    if len(raw) > MAX_OUTPUT_BYTES:
        return Unavailable(reason_code="provider_output_too_large")
    try:
        decoded = raw.decode("utf-8")
        json.loads(decoded, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, RecursionError):
        return Unavailable(reason_code="invalid_provider_json")
    try:
        return ProviderOutput.model_validate_json(decoded)
    except ValidationError:
        return Unavailable(reason_code="invalid_provider_schema")


class ClassificationService:
    def __init__(self, adapter: InferenceAdapter | None = None, *, timeout_seconds: float = 3.0):
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10:
            raise ValueError("Invalid classification deadline")
        self._adapter = adapter
        self._timeout = timeout_seconds

    async def classify(self, payload: object) -> ClassificationResult:
        try:
            message = ClassificationInput.model_validate(payload)
        except ValidationError:
            return Rejected()
        if self._adapter is None:
            return Unavailable(reason_code="provider_unconfigured")
        try:
            async with asyncio.timeout(self._timeout) as deadline:
                raw = await self._adapter.infer(message)
            if deadline.expired():
                return Unavailable(reason_code="provider_timeout")
            result = validate_provider_output(raw)
        except TimeoutError:
            return Unavailable(reason_code="provider_timeout")
        except ProviderUnavailable:
            return Unavailable(reason_code="provider_unavailable")
        except ProviderOutputTooLarge:
            return Unavailable(reason_code="provider_output_too_large")
        except Exception:
            return Unavailable(reason_code="unexpected_failure")
        if isinstance(result, Unavailable):
            return result
        if not result.languages:
            return Uncertain(languages=(), reason_code="language_unknown")
        confidence = result.relevance_confidence
        if confidence is None or confidence < CLARIFY_BELOW:
            return Uncertain(languages=result.languages, reason_code="relevance_uncertain")
        category = next(label for label in CATEGORY_ORDER if label in result.relevance_categories)
        return Classified(languages=result.languages, relevance_category=category, relevance_confidence=confidence)
