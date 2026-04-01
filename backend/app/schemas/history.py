"""History record related Pydantic models."""

from datetime import datetime

from pydantic import Field

from app.schemas.base import SchemaBase


class Summary(SchemaBase):
    ai: int = Field(..., ge=0, le=100, json_schema_extra={"example": 45}, description="AI percentage in [0, 100]")
    mixed: int = Field(..., ge=0, le=100, json_schema_extra={"example": 25}, description="Mixed percentage in [0, 100]")
    human: int = Field(..., ge=0, le=100, json_schema_extra={"example": 30}, description="Human percentage in [0, 100]")


class Sentence(SchemaBase):
    id: str = Field(..., json_schema_extra={"example": "sent-1"}, description="Segment identifier")
    text: str = Field(..., json_schema_extra={"example": "The quick brown fox jumps over the lazy dog."}, description="Normalized segment text")
    raw: str = Field(..., json_schema_extra={"example": "The quick brown fox jumps over the lazy dog."}, description="Original segment text")
    start_paragraph: int | None = Field(
        default=None,
        ge=1,
        json_schema_extra={"example": 2},
        description="1-based start paragraph index covered by this segment",
    )
    end_paragraph: int | None = Field(
        default=None,
        ge=1,
        json_schema_extra={"example": 4},
        description="1-based end paragraph index covered by this segment",
    )
    type: str = Field(..., json_schema_extra={"example": "ai"}, description="Segment type: ai/mixed/human")
    probability: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.85}, description="AI probability in [0, 1]")
    score: int = Field(..., ge=0, le=100, json_schema_extra={"example": 85}, description="Display score in [0, 100]")
    reason: str = Field(..., json_schema_extra={"example": "Highly structured phrasing"}, description="Explanation")
    suggestion: str = Field(..., json_schema_extra={"example": "Use a more natural rewrite"}, description="Suggestion")


class Citation(SchemaBase):
    id: str = Field(..., json_schema_extra={"example": "cite-1"}, description="Citation identifier")
    text: str = Field(..., json_schema_extra={"example": "Quoted excerpt"}, description="Citation excerpt")
    source: str = Field(..., json_schema_extra={"example": "Example Source"}, description="Citation source")


class Analysis(SchemaBase):
    summary: Summary = Field(..., description="Summary percentages")
    sentences: list[Sentence] = Field(..., description="Segment-level analysis results")
    translation: str = Field(default="", description="Translation result")
    polish: str = Field(default="", description="Polish suggestion")
    citations: list[Citation] = Field(default_factory=list, description="Citation results")
    ai_likely_count: int = Field(..., ge=0, description="Count of segments likely to be AI-generated")
    highlighted_html: str = Field(..., description="Highlighted preview HTML")


class HistoryRecordCreate(SchemaBase):
    title: str = Field(..., max_length=255, json_schema_extra={"example": "Scan Record · 2026-02-11 13:15:21"}, description="Record title")
    functions: list[str] = Field(..., json_schema_extra={"example": ["scan"]}, description="Enabled functions")
    input_text: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        json_schema_extra={"example": "The quick brown fox..."},
        description="Original input text",
    )
    editor_html: str = Field(..., json_schema_extra={"example": "<p>The quick brown fox...</p>"}, description="Editor HTML content")
    analysis: Analysis | None = Field(None, description="Analysis result")


class ClaimGuestHistoryRequest(SchemaBase):
    guest_token: str = Field(..., min_length=1, description="Guest JWT")


class ClaimGuestHistoryResponse(SchemaBase):
    claimed_count: int = Field(..., ge=0, json_schema_extra={"example": 3}, description="Number of claimed records")


class HistoryRecordUpdate(SchemaBase):
    title: str = Field(..., max_length=255, json_schema_extra={"example": "New Title"}, description="Updated title")


class HistoryRecordResponse(SchemaBase):
    id: int = Field(..., json_schema_extra={"example": 1}, description="History record ID")
    user_id: int = Field(..., json_schema_extra={"example": 123}, description="Owner user ID")
    title: str | None = Field(None, json_schema_extra={"example": "Scan Record · 2026-02-11 13:15:21"}, description="Record title")
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-02-11T13:15:21.000Z"}, description="Creation timestamp")
    functions: list[str] = Field(default_factory=list, json_schema_extra={"example": ["scan"]}, description="Enabled functions")
    input_text: str = Field(..., json_schema_extra={"example": "The quick brown fox..."}, description="Original input text")
    editor_html: str | None = Field(None, json_schema_extra={"example": "<p>The quick brown fox...</p>"}, description="Editor HTML content")
    analysis: Analysis | None = Field(None, description="Analysis result")


class HistoryListResponse(SchemaBase):
    items: list[HistoryRecordResponse] = Field(..., description="History records")
    total: int = Field(..., json_schema_extra={"example": 50}, description="Total count")
    page: int = Field(..., json_schema_extra={"example": 1}, description="Current page")
    per_page: int = Field(..., json_schema_extra={"example": 20}, description="Items per page")
    total_pages: int = Field(..., json_schema_extra={"example": 3}, description="Total pages")


class BatchDeleteRequest(SchemaBase):
    ids: list[int] = Field(..., min_length=1, json_schema_extra={"example": [1, 2, 3]}, description="History record IDs to delete")


class BatchDeleteResponse(SchemaBase):
    deleted_count: int = Field(..., json_schema_extra={"example": 2}, description="Deleted record count")
    failed_ids: list[int] = Field(default_factory=list, json_schema_extra={"example": []}, description="IDs that could not be deleted")


class ClearAllResponse(SchemaBase):
    deleted_count: int = Field(..., json_schema_extra={"example": 50}, description="Deleted record count")
