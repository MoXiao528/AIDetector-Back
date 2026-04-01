"""Schemas for scan examples."""

from pydantic import Field

from app.schemas.base import SchemaBase


class ScanHeroExampleItem(SchemaBase):
    key: str
    label: str
    content: str
    description: str | None = None
    ai: int | None = Field(default=None, ge=0, le=100)
    mixed: int | None = Field(default=None, ge=0, le=100)
    human: int | None = Field(default=None, ge=0, le=100)
    snapshot: str | None = None
    snippet: str | None = None
    structure: str | None = None
    rhythm: str | None = None
    action: str | None = None


class ScanUsageExampleItem(SchemaBase):
    key: str
    title: str
    doc_type: str
    length: str
    description: str
    ai: int = Field(..., ge=0, le=100)
    mixed: int = Field(..., ge=0, le=100)
    human: int = Field(..., ge=0, le=100)
    snapshot: str
    snippet: str
    content: str | None = None


class ScanExamplesResponse(SchemaBase):
    locale: str
    hero_examples: list[ScanHeroExampleItem]
    usage_examples: list[ScanUsageExampleItem]
