"""Server-owned Evidence output; never used by history/detection input schemas."""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.core.config import get_settings
from app.schemas.base import SchemaBase
from app.services.evidence_engine import METRICS


UnitInterval = Annotated[float, Field(ge=0, le=1)]
Percentile = Annotated[float, Field(ge=0, le=100)]
Reason = Literal[
    "invalid_evidence_config",
    "invalid_evidence_bundle",
    "bundle_unavailable",
    "invalid_router_response",
    "invalid_text",
    "invalid_main_label",
    "feature_extraction_failed",
    "comparison_failed",
    "model_unavailable",
    "model_failure",
    "busy",
    "timeout",
    "language_undetermined",
    "unsupported_language",
    "below_minimum_length",
    "fewer_than_10_sentences",
    "excluded_content_over_40pct",
    "length_out_of_range",
    "reference_cell_unavailable",
    "reference_fallback_language_length",
    "reference_fallback_language",
    "missing_observations",
    "reference_metrics_unavailable",
    "no_comparable_metrics",
    "no_valid_source_groups",
    "fewer_than_10_source_groups",
    "not_applicable_for_language",
    "insufficient_observations",
]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, revalidate_instances="always"
    )


class EvidenceOffset(_EvidenceModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Invalid Evidence offset")
        return self


class EvidencePattern(_EvidenceModel):
    count: int = Field(ge=1)
    offsets: list[EvidenceOffset]


class EvidencePatterns(_EvidenceModel):
    descriptive_top_tokens: list[EvidencePattern]
    repeated_phrases: list[EvidencePattern]
    sentence_start_templates: list[EvidencePattern]


class EvidenceConfidence(_EvidenceModel):
    language: UnitInterval
    domain: UnitInterval


class EvidenceRoute(_EvidenceModel):
    language: Literal["ar", "de", "en", "es", "fr", "pt", "ru", "zh"]
    domain: Literal["academic", "news", "novel", "seo", "webtext", "wiki"]
    confidence: EvidenceConfidence
    lengthBucket: Literal["below_minimum", "short", "medium", "long", "above_long"]
    fallbackLevel: Literal["exact", "language_length", "language", "unavailable"]


class EvidenceRanges(_EvidenceModel):
    human: list[float] = Field(min_length=2, max_length=2)
    ai: list[float] = Field(min_length=2, max_length=2)


class EvidenceRelation(_EvidenceModel):
    human: Literal["below", "within", "above"]
    ai: Literal["below", "within", "above"]


class EvidenceSignal(_EvidenceModel):
    dimension: Literal["lexical", "phrase_template", "rhythm", "discourse"]
    metric: str
    observed: float | None
    humanPercentile: Percentile | None
    aiPercentile: Percentile | None
    referenceRanges: EvidenceRanges | None
    relation: EvidenceRelation | None
    notice: Literal["reference_mismatch", "outside_both"] | None
    sampleCount: Annotated[int, Field(ge=0)] | None
    offsets: list[EvidenceOffset]
    reasons: list[Reason]

    @model_validator(mode="after")
    def known_metric(self):
        if METRICS.get(self.metric) != self.dimension:
            raise ValueError("Invalid Evidence metric")
        return self


class EvidenceQuality(_EvidenceModel):
    level: Literal["ready", "partial", "insufficient", "unavailable"]
    coverage: UnitInterval
    reasons: list[Reason]


class EvidenceResult(_EvidenceModel):
    status: Literal["ready", "partial", "insufficient", "unsupported", "failed"]
    artifactVersion: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    featureSchemaVersion: Literal[1]
    route: EvidenceRoute | None
    quality: EvidenceQuality
    signals: list[EvidenceSignal]
    patterns: EvidencePatterns | None

    @field_validator("featureSchemaVersion", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Invalid Evidence version")
        return value

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status in {"failed", "unsupported"}:
            valid = (
                self.quality.level == "unavailable"
                and self.quality.coverage == 0
                and self.route is None
                and not self.signals
                and self.patterns is None
            )
        else:
            comparable = sum(signal.relation is not None for signal in self.signals)
            level = (
                "ready"
                if comparable == len(METRICS)
                else "partial"
                if comparable
                else "insufficient"
            )
            valid = (
                self.artifactVersion is not None
                and self.route is not None
                and self.patterns is not None
                and [signal.metric for signal in self.signals] == list(METRICS)
                and self.status == self.quality.level == level
                and self.quality.coverage == comparable / len(METRICS)
            )
        if not valid:
            raise ValueError("Invalid Evidence result")
        return self


def validate_evidence_snapshot(value: Any) -> EvidenceResult | None:
    """Validate stored or newly computed data independently of the public mode."""
    if value is None:
        return None
    try:
        return EvidenceResult.model_validate(value)
    except Exception:
        return None


def project_public_evidence(value: Any) -> EvidenceResult | None:
    """Read snapshots without loading artifacts, changing storage or exposing errors."""
    if get_settings().detect_evidence_mode != "serve":
        return None
    return validate_evidence_snapshot(value)


class EvidenceResponseBase(SchemaBase):
    evidence: EvidenceResult | None = Field(
        default=None,
        description="Server Evidence snapshot. Omitted outside serve mode, when absent or invalid.",
    )

    @field_validator("evidence", mode="before")
    @classmethod
    def project_evidence(cls, value):
        return project_public_evidence(value)

    @model_serializer(mode="wrap")
    def omit_hidden_evidence(self, handler):
        result = handler(self)
        # Preserve all existing null fields; only Evidence is conditionally omitted.
        if self.evidence is None or get_settings().detect_evidence_mode != "serve":
            result.pop("evidence", None)
        return result
