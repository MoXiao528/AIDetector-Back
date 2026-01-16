"""Analysis-related schemas for /api/scan/detect."""

from pydantic import Field

from app.schemas.base import SchemaBase


class DetectRequest(SchemaBase):
    text: str = Field(..., min_length=1, max_length=20000, example="Sample text to analyze.")
    functions: list[str] | None = Field(
        default=None,
        description='Optional feature switches. Defaults to include "scan".',
        example=["scan", "polish", "translation", "citations"],
    )


class SentenceAnalysis(SchemaBase):
    text: str = Field(..., example="This is a sentence.")
    is_ai: bool = Field(..., example=False)
    confidence: float = Field(..., ge=0, le=1, example=0.12)


class Citation(SchemaBase):
    source: str = Field(..., example="Example Source")
    snippet: str = Field(..., example="Relevant excerpt")
    url: str | None = Field(default=None, example="https://example.com")


class AnalysisResponse(SchemaBase):
    summary: str = Field(..., example="Overall analysis summary.")
    sentences: list[SentenceAnalysis] = Field(default_factory=list)
    polish: str | None = Field(default=None, example="Polished text output.")
    translation: str | None = Field(default=None, example="Translated text output.")
    citations: list[Citation] | None = Field(default=None)
    currentCredits: int = Field(..., example=9990, description="Remaining credits after deduction")
