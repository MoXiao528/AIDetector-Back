"""History record related Pydantic models."""

from datetime import datetime
from typing import Optional

from pydantic import Field

from app.schemas.base import SchemaBase


class Summary(SchemaBase):
    """摘要统计: AI/混合/人工百分比"""
    ai: int = Field(..., ge=0, le=100, json_schema_extra={"example": 45}, description="AI 生成的百分比 (0-100)")
    mixed: int = Field(..., ge=0, le=100, json_schema_extra={"example": 25}, description="混合内容的百分比 (0-100)")
    human: int = Field(..., ge=0, le=100, json_schema_extra={"example": 30}, description="人工撰写的百分比 (0-100)")


class Sentence(SchemaBase):
    """句子级别的分析结果"""
    id: str = Field(..., json_schema_extra={"example": "sent-1"}, description="句子唯一标识")
    text: str = Field(..., json_schema_extra={"example": "The quick brown fox jumps over the lazy dog."}, description="句子文本内容")
    raw: str = Field(..., json_schema_extra={"example": "The quick brown fox jumps over the lazy dog."}, description="原始文本（包含可能的前后空格）")
    type: str = Field(..., json_schema_extra={"example": "ai"}, description="分类结果: ai/mixed/human")
    probability: float = Field(..., ge=0, le=1, json_schema_extra={"example": 0.85}, description="概率值 (0-1)")
    score: int = Field(..., ge=0, le=100, json_schema_extra={"example": 85}, description="分数 (0-100)")
    reason: str = Field(..., json_schema_extra={"example": "高度结构化的表达方式"}, description="判断理由")
    suggestion: str = Field(..., json_schema_extra={"example": "可以使用更加口语化的表述"}, description="改进建议")


class Citation(SchemaBase):
    """引用检测结果"""
    id: str = Field(..., json_schema_extra={"example": "cite-1"}, description="引用唯一标识")
    text: str = Field(..., json_schema_extra={"example": "引用的文本片段"}, description="引用的文本片段")
    source: str = Field(..., json_schema_extra={"example": "来源说明"}, description="来源说明")


class Analysis(SchemaBase):
    """完整的分析结果"""
    summary: Summary = Field(..., description="摘要统计")
    sentences: list[Sentence] = Field(..., description="句子级别的分析结果")
    translation: str = Field(default="", description="翻译结果（如果使用了翻译功能）")
    polish: str = Field(default="", description="润色建议（如果使用了润色功能）")
    citations: list[Citation] = Field(default_factory=list, description="引用检测结果（如果使用了引用功能）")
    ai_likely_count: int = Field(..., ge=0, description="AI 生成可能性高的句子数量")
    highlighted_html: str = Field(..., description="高亮标注后的 HTML")


class HistoryRecordCreate(SchemaBase):
    """创建历史记录请求"""
    title: str = Field(..., max_length=255, json_schema_extra={"example": "扫描记录 · 2026-02-11 13:15:21"}, description="记录标题")
    functions: list[str] = Field(..., json_schema_extra={"example": ["scan"]}, description="使用的功能列表")
    input_text: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        json_schema_extra={"example": "The quick brown fox..."},
        description="原始输入文本",
    )
    editor_html: str = Field(..., json_schema_extra={"example": "<p>The quick brown fox...</p>"}, description="富文本编辑器的 HTML 内容")
    analysis: Optional[Analysis] = Field(None, description="分析结果（可选）")


class ClaimGuestHistoryRequest(SchemaBase):
    """认领游客历史记录请求"""
    guest_token: str = Field(..., min_length=1, description="游客 JWT")


class ClaimGuestHistoryResponse(SchemaBase):
    """认领游客历史记录响应"""
    claimed_count: int = Field(..., ge=0, json_schema_extra={"example": 3}, description="成功认领的记录数")


class HistoryRecordUpdate(SchemaBase):
    """更新历史记录请求（仅允许更新 title）"""
    title: str = Field(..., max_length=255, json_schema_extra={"example": "新标题"}, description="新的记录标题")


class HistoryRecordResponse(SchemaBase):
    """历史记录响应"""
    id: int = Field(..., json_schema_extra={"example": 1}, description="历史记录 ID")
    user_id: int = Field(..., json_schema_extra={"example": 123}, description="所属用户 ID")
    title: Optional[str] = Field(None, json_schema_extra={"example": "扫描记录 · 2026-02-11 13:15:21"}, description="记录标题")
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-02-11T13:15:21.000Z"}, description="创建时间，ISO 8601 格式")
    functions: list[str] = Field(default_factory=list, json_schema_extra={"example": ["scan"]}, description="使用的功能列表")
    input_text: str = Field(..., json_schema_extra={"example": "The quick brown fox..."}, description="原始输入文本")
    editor_html: Optional[str] = Field(None, json_schema_extra={"example": "<p>The quick brown fox...</p>"}, description="富文本编辑器的 HTML 内容")
    analysis: Optional[Analysis] = Field(None, description="分析结果（可选）")


class HistoryListResponse(SchemaBase):
    """历史记录列表响应"""
    items: list[HistoryRecordResponse] = Field(..., description="历史记录列表")
    total: int = Field(..., json_schema_extra={"example": 50}, description="总记录数")
    page: int = Field(..., json_schema_extra={"example": 1}, description="当前页码")
    per_page: int = Field(..., json_schema_extra={"example": 20}, description="每页数量")
    total_pages: int = Field(..., json_schema_extra={"example": 3}, description="总页数")


class BatchDeleteRequest(SchemaBase):
    """批量删除请求"""
    ids: list[int] = Field(..., min_length=1, json_schema_extra={"example": [1, 2, 3]}, description="要删除的历史记录 ID 列表")


class BatchDeleteResponse(SchemaBase):
    """批量删除响应"""
    deleted_count: int = Field(..., json_schema_extra={"example": 2}, description="成功删除的记录数")
    failed_ids: list[int] = Field(default_factory=list, json_schema_extra={"example": []}, description="删除失败的 ID 列表")


class ClearAllResponse(SchemaBase):
    """清空所有历史记录响应"""
    deleted_count: int = Field(..., json_schema_extra={"example": 50}, description="删除的记录数")
