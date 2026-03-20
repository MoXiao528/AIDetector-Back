"""Service helpers for scan examples."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scan_example import ScanExample
from app.schemas.scan_example import ScanExamplesResponse, ScanHeroExampleItem, ScanUsageExampleItem

DEFAULT_EXAMPLES = [
    {
        "locale": "zh-CN",
        "placement": "hero",
        "key": "chatgpt",
        "label": "ChatGPT",
        "description": "典型的高度结构化 AI 说明型段落。",
        "content": "人工智能系统正在以前所未有的速度推动信息生成，这使得教育工作者越来越难以区分真实的学生写作与由生成式模型产出的文本内容。",
        "sort_order": 10,
    },
    {
        "locale": "zh-CN",
        "placement": "hero",
        "key": "human",
        "label": "人工写作",
        "description": "带有个人观察、停顿和经验细节的人类表达。",
        "content": "我在访谈每一位学生时，都会留意他们停顿时的迟疑、举例时的生活细节，以及他们如何把课堂讨论和自己的真实经历连接起来。",
        "sort_order": 20,
    },
    {
        "locale": "zh-CN",
        "placement": "hero",
        "key": "hybrid",
        "label": "AI + 人工",
        "description": "先由 AI 起草，再由人工补足证据和风格。",
        "content": "初始提纲由 AI 助手生成，但我重新撰写了每一段内容，补入最近期刊的引用，并按学院写作规范把语气调整得更克制、更正式。",
        "sort_order": 30,
    },
    {
        "locale": "zh-CN",
        "placement": "hero",
        "key": "polished",
        "label": "AI 润色",
        "description": "原文经过 AI 润色后的更规整版本。",
        "content": "完成报告后，系统建议我合并重复表述并提升学术语气，修订后的段落因此在逻辑衔接和语言凝练度上都明显更强。",
        "sort_order": 40,
    },
    {
        "locale": "zh-CN",
        "placement": "usage",
        "key": "thesis",
        "title": "教育心理学论文段落",
        "content": "本研究通过对 46 份课堂观察记录进行扎根理论编码，进一步提炼出互动质量指标与学生自我效能之间的非线性关系，并结合访谈材料验证教师反馈在其中的调节作用。",
        "description": "研究生论文关于课堂互动的论证段落，包含引用与方法描述。",
        "doc_type": "Academic",
        "length_label": "1200 words",
        "ai": 22,
        "mixed": 18,
        "human": 60,
        "snapshot": "章节摘要 + 标色句子",
        "snippet": "本研究通过对 46 份课堂观察记录进行扎根理论编码，进一步提炼出互动质量指标与学生自我效能之间的非线性关系。",
        "sort_order": 110,
    },
    {
        "locale": "zh-CN",
        "placement": "usage",
        "key": "marketing",
        "title": "品牌营销落地页",
        "content": "我们为教师提供统一的反馈模板与语气库，让跨课程的评分标准更加一致，同时保留个性化的书写空间，并通过协作看板帮助教学团队同步处理高风险作业。",
        "description": "面向高校的 AI 评分助手推广页，突出效率提升与团队协作。",
        "doc_type": "Marketing",
        "length_label": "780 words",
        "ai": 35,
        "mixed": 28,
        "human": 37,
        "snapshot": "AI 概率分布 + CTA 建议",
        "snippet": "我们为教师提供统一的反馈模板与语气库，让跨课程的评分标准更加一致，同时保留个性化的书写空间。",
        "sort_order": 120,
    },
    {
        "locale": "zh-CN",
        "placement": "usage",
        "key": "technical",
        "title": "技术设计文档",
        "content": "为避免批量检测阻塞，我们将文件分片上传并行处理，并在队列服务中设置退避重试策略以稳定延迟；同时通过优先级队列隔离长文档任务，降低峰值抖动。",
        "description": "数据清洗流水线的设计提案，包含风险提示与性能指标。",
        "doc_type": "Technical",
        "length_label": "6 pages",
        "ai": 18,
        "mixed": 24,
        "human": 58,
        "snapshot": "风险提示 + 改写建议",
        "snippet": "为避免批量检测阻塞，我们将文件分片上传并行处理，并在队列服务中设置退避重试策略以稳定延迟。",
        "sort_order": 130,
    },
    {
        "locale": "en-US",
        "placement": "hero",
        "key": "chatgpt",
        "label": "ChatGPT",
        "description": "A highly structured AI-generated explanatory passage.",
        "content": "Artificial intelligence systems have rapidly accelerated the pace of information creation, challenging educators to distinguish authentic student work from generated text.",
        "sort_order": 10,
    },
    {
        "locale": "en-US",
        "placement": "hero",
        "key": "human",
        "label": "Human",
        "description": "Human writing with personal observations and lived detail.",
        "content": "When I interviewed each student, I paid attention to their pauses, their personal anecdotes, and the way they connected class discussions to their own experiences.",
        "sort_order": 20,
    },
    {
        "locale": "en-US",
        "placement": "hero",
        "key": "hybrid",
        "label": "AI + Human",
        "description": "AI drafted first, then a human revised for evidence and voice.",
        "content": "The initial outline was produced by an AI assistant, but I rewrote each paragraph to weave in citations from recent journals and to adjust the tone to match our faculty guidelines.",
        "sort_order": 30,
    },
    {
        "locale": "en-US",
        "placement": "hero",
        "key": "polished",
        "label": "Polished by AI",
        "description": "A passage refined by AI for clarity and cohesion.",
        "content": "After running the report, the assistant suggested consolidating redundant sentences and elevating the academic voice; the revised excerpt now reads with greater clarity and cohesion.",
        "sort_order": 40,
    },
    {
        "locale": "en-US",
        "placement": "usage",
        "key": "thesis",
        "title": "Educational psychology thesis excerpt",
        "content": "This study coded 46 classroom observation records with grounded theory to surface the nonlinear relationship between interaction quality and student self-efficacy, then validated the moderating role of teacher feedback with interview evidence.",
        "description": "A graduate thesis passage about classroom interaction with citations and method notes.",
        "doc_type": "Academic",
        "length_label": "1200 words",
        "ai": 22,
        "mixed": 18,
        "human": 60,
        "snapshot": "Section summary + highlighted sentences",
        "snippet": "This study coded 46 classroom observation records with grounded theory to surface the nonlinear relationship between interaction quality and student self-efficacy.",
        "sort_order": 110,
    },
    {
        "locale": "en-US",
        "placement": "usage",
        "key": "marketing",
        "title": "Brand landing page copy",
        "content": "We give instructors shared feedback templates and tone libraries so grading standards stay aligned across courses without flattening individual teaching styles, while a collaborative review board helps teams triage high-risk submissions faster.",
        "description": "A landing page for an AI grading assistant aimed at universities.",
        "doc_type": "Marketing",
        "length_label": "780 words",
        "ai": 35,
        "mixed": 28,
        "human": 37,
        "snapshot": "AI distribution + CTA guidance",
        "snippet": "We give instructors shared feedback templates and tone libraries so grading standards stay aligned across courses without flattening individual teaching styles.",
        "sort_order": 120,
    },
    {
        "locale": "en-US",
        "placement": "usage",
        "key": "technical",
        "title": "Technical design document",
        "content": "To prevent batch scans from stalling, we upload file shards in parallel and apply exponential backoff in the queue service to stabilize latency, while a priority lane isolates oversized document jobs during traffic spikes.",
        "description": "A data-cleaning pipeline proposal with risk controls and performance targets.",
        "doc_type": "Technical",
        "length_label": "6 pages",
        "ai": 18,
        "mixed": 24,
        "human": 58,
        "snapshot": "Risk flags + rewrite guidance",
        "snippet": "To prevent batch scans from stalling, we upload file shards in parallel and apply exponential backoff in the queue service to stabilize latency.",
        "sort_order": 130,
    },
]


class ScanExampleService:
    def __init__(self, db: Session):
        self.db = db

    def list_examples(self, locale: str) -> ScanExamplesResponse:
        normalized_locale = self._normalize_locale(locale)
        self._ensure_seeded()

        records = self._fetch_records(normalized_locale)
        response_locale = normalized_locale
        if not records and normalized_locale != "en-US":
            records = self._fetch_records("en-US")
            response_locale = "en-US"

        hero_examples = [
            ScanHeroExampleItem(
                key=item.key,
                label=item.label or item.title or item.key,
                content=item.content or "",
                description=item.description,
            )
            for item in records
            if item.placement == "hero" and item.content
        ]
        usage_examples = [
            ScanUsageExampleItem(
                key=item.key,
                title=item.title or item.label or item.key,
                doc_type=item.doc_type or "General",
                length=item.length_label or "",
                description=item.description or "",
                ai=item.ai or 0,
                mixed=item.mixed or 0,
                human=item.human or 0,
                snapshot=item.snapshot or "",
                snippet=item.snippet or "",
                content=item.content,
            )
            for item in records
            if item.placement == "usage"
        ]

        return ScanExamplesResponse(
            locale=response_locale,
            hero_examples=hero_examples,
            usage_examples=usage_examples,
        )

    def _fetch_records(self, locale: str) -> list[ScanExample]:
        stmt = (
            select(ScanExample)
            .where(ScanExample.locale == locale, ScanExample.is_active.is_(True))
            .order_by(ScanExample.sort_order.asc(), ScanExample.id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def _ensure_seeded(self) -> None:
        existing_records = {
            (record.locale, record.placement, record.key): record
            for record in self.db.scalars(select(ScanExample)).all()
        }
        pending = []
        changed = False
        for item in DEFAULT_EXAMPLES:
            identity = (item["locale"], item["placement"], item["key"])
            existing = existing_records.get(identity)
            if existing:
                for field, value in item.items():
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if existing.is_active is not True:
                    existing.is_active = True
                    changed = True
                continue
            pending.append(
                ScanExample(
                    is_active=True,
                    **item,
                )
            )

        if not pending and not changed:
            return

        if pending:
            self.db.add_all(pending)
        self.db.commit()

    @staticmethod
    def _normalize_locale(locale: str | None) -> str:
        if locale in {"zh-CN", "en-US"}:
            return locale
        if locale and locale.startswith("zh"):
            return "zh-CN"
        return "en-US"
