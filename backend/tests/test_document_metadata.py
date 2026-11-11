from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pypdf import PdfReader

from app.api.v1 import detections, reports
from app.api.v1.admin import _extract_analysis
from app.api.v1.guest_history import _require_record
from app.api.v1.history import _detection_to_history_response
from app.db.deps import ActorContext
from app.models.detection import Detection
from app.models.user import User
from app.schemas.admin import AdminDetectionDetailResponse
from app.schemas.detection import DetectionItem, DetectionRequest
from app.schemas.history import project_document_analysis, project_public_meta_json
from app.schemas.report import ReportPdfContent, ReportPdfRequest
from app.services.document_metadata import document_paragraphs, metadata_paragraph_indexes
from app.services.report_pdf import build_report_pdf


@pytest.fixture
def anyio_backend():
    return "asyncio"


def saved_analysis(text):
    paragraphs = document_paragraphs(text)
    return {
        "summary": {"ai": 23, "human": 77},
        "sentences": [
            {"id": f"para-{i}", "text": p, "raw": p, "start_paragraph": i + 1, "end_paragraph": i + 1,
             "type": "ai", "probability": 0.68, "score": 68, "reason": "saved", "suggestion": "saved"}
            for i, p in enumerate(paragraphs)
        ],
        "ai_likely_count": len(paragraphs),
        "highlighted_html": '<p><span class="bg-orange-100">Old highlight</span></p>',
    }


def saved_record(text="背影\n作者: 朱自清\n我与父亲不相见已二年余了。", editor_html=None):
    return SimpleNamespace(
        id=490, user_id=1, title="record", created_at=datetime.now(timezone.utc),
        functions_used=["scan"], input_text=text, editor_html=editor_html, is_pinned=False,
        result_label="human", score=0.1, actor_type="user", actor_id="1", chars_used=len(text),
        meta_json={"analysis": saved_analysis(text), "options": {"repre_guard": {"segments": [{"token_count": 256, "raw_score": 0.02}]}},
                   "evidence": {"frozen": "unmodified"}},
    )


@pytest.mark.parametrize("byline", ["作者: 朱自清", "作者：朱自清", "Author: John Smith", "AUTHOR：J. R. R. Tolkien"])
def test_opening_title_and_byline_are_metadata(byline):
    assert metadata_paragraph_indexes(f"\r\n 背影 \r\n\r\n {byline} \n\n正文。") == {0, 1}


@pytest.mark.parametrize("text", [
    "好。\n不去！\n我答道。", "普通短标题\n这是正文。\n第三段。",
    "开头。\n作者: 朱自清\n正文。", "正文首段。\n背影\n作者: 朱自清\n正文末段。",
    "标题\n作者说这是他写的。\n正文。", "标题\n作者: 朱自清写道：这里是正文。\n正文。",
    "标题\nBy the river we stopped to rest\n正文。", "长" * 61 + "\n作者: 朱自清\n正文。",
])
def test_short_prose_dialogue_and_body_author_mentions_are_not_metadata(text):
    assert metadata_paragraph_indexes(text) == set()


@pytest.mark.parametrize("level", range(1, 7))
def test_html_headings_use_positions_and_preserve_repeated_body_text(level):
    text = "相同短句\n相同短句\n正文。"
    html = f"<h{level}><strong>相同短句</strong></h{level}><p>相同短句</p><p>正文。</p>"
    assert metadata_paragraph_indexes(text, html) == {0}
    projected = project_document_analysis(saved_analysis(text), text, html)
    assert [s.type for s in projected.sentences] == ["metadata", "ai", "ai"]
    assert projected.sentences[1].score == 68
    assert projected.highlighted_html.startswith("<p>相同短句</p>")


@pytest.mark.parametrize("html", [
    "<h1>正文。</h1>", "<h1>无关内容</h1><p>正文。</p>",
    "<h1>标题<p>正文。</p>", "<h1>标题</p>正文。</h1>",
])
def test_unrelated_or_malformed_heading_html_cannot_hide_body(html):
    assert metadata_paragraph_indexes("标题\n正文。", html) == set()


def test_projection_keeps_full_document_summary_and_frozen_snapshot():
    record = saved_record()
    before = deepcopy(record.meta_json)
    result = project_document_analysis(record.meta_json["analysis"], record.input_text)
    assert [s.type for s in result.sentences] == ["metadata", "metadata", "ai"]
    assert result.ai_likely_count == 1
    assert result.summary.model_dump() == before["analysis"]["summary"]
    assert all((s.score, s.probability) == (0, 0) for s in result.sentences[:2])
    assert result.highlighted_html.startswith("<p>背影</p><p>作者: 朱自清</p>")
    assert record.meta_json == before
    public = project_public_meta_json(record.meta_json, record.input_text)
    assert public["analysis"]["ai_likely_count"] == 1
    assert public["options"] == before["options"]
    assert record.meta_json == before


def test_legacy_merged_block_is_not_neutralized_over_body_text():
    text = "背影\n作者: 朱自清\n正文依旧参与判断。"
    analysis = saved_analysis(text)
    merged = {**analysis["sentences"][0], "raw": text, "text": text, "end_paragraph": 3}
    analysis["sentences"] = [merged]
    projected = project_document_analysis(analysis, text)
    assert projected.sentences[0].type == "ai"
    assert projected.sentences[0].probability == 0.68
    assert projected.highlighted_html == analysis["highlighted_html"]


def test_indexless_legacy_paragraphs_require_full_positional_alignment():
    record = saved_record()
    analysis = record.meta_json["analysis"]
    for segment in analysis["sentences"]:
        segment.pop("start_paragraph")
        segment.pop("end_paragraph")
    assert project_document_analysis(analysis, record.input_text).sentences[0].type == "metadata"
    analysis["sentences"][0]["raw"] = "不对应的文本"
    assert project_document_analysis(analysis, record.input_text).sentences[0].type == "ai"


def test_history_guest_admin_and_detection_list_share_projection_without_mutation():
    record = saved_record("Heading\nBody.", "<h2>Heading</h2><p>Body.</p>")
    before = deepcopy(record.meta_json)
    for response in (_detection_to_history_response(record), _require_record(record)):
        assert response.analysis.sentences[0].type == "metadata"
    assert _extract_analysis(record).sentences[0].type == "metadata"
    listed = DetectionItem.from_orm_detection(record)
    assert listed.meta_json["analysis"]["sentences"][0]["type"] == "metadata"
    admin = AdminDetectionDetailResponse(
        id=record.id, actor_type=record.actor_type, actor_id=record.actor_id, label=record.result_label,
        score=record.score, chars_used=record.chars_used, created_at=record.created_at,
        input_text=record.input_text, editor_html=record.editor_html, meta_json=record.meta_json,
        analysis=_extract_analysis(record),
    )
    assert admin.meta_json["analysis"]["sentences"][0]["type"] == "metadata"
    assert admin.analysis.sentences[0].type == "metadata"
    assert record.meta_json == before


def test_replay_projects_legacy_snapshot_without_rewriting_it(monkeypatch):
    record = saved_record()
    before = deepcopy(record.meta_json)
    monkeypatch.setattr(detections, "get_effective_used_today", lambda *args, **kwargs: 0)
    db = SimpleNamespace(get=lambda model, identifier: record)
    response = detections._replay_detection_response(db, detection_id=490, actor_type="user", actor_id="1")
    assert [s.type for s in response.result.sentences] == ["metadata", "metadata", "ai"]
    assert response.score == record.score
    assert record.meta_json == before
    compatibility = detections._build_scan_analysis_response(response)
    assert compatibility.sentences[0].type == "metadata"


@pytest.mark.anyio
async def test_new_detection_keeps_inference_text_and_raw_blocks(db_session, unique_email, monkeypatch):
    user = User(email=unique_email, name="reader", password_hash="unused")
    db_session.add(user)
    db_session.flush()
    text = "背影\n作者: 朱自清\n" + " ".join(["body"] * 254)
    sent = []

    async def detect(text):
        sent.append(text)
        return {"score": 0.9, "threshold": 0.5, "label": "AI", "model_name": "test", "score_type": "probability"}

    monkeypatch.setattr(detections.repre_guard_client, "detect", detect)
    response = await detections.detect(
        payload=DetectionRequest(text=text), db=db_session,
        current_actor=ActorContext(actor_type="user", actor_id=str(user.id), user=user), idempotency_key=uuid4(),
    )
    assert sent == [text]
    assert response.score == 0.9
    assert response.result.summary.ai == 100
    assert response.result.ai_likely_count == 1
    assert [s.type for s in response.result.sentences] == ["metadata", "metadata", "ai"]
    saved = db_session.get(Detection, response.detection_id)
    assert saved.meta_json["analysis"]["sentences"][0]["type"] == "metadata"
    assert saved.meta_json["options"]["repre_guard"]["segments"][0]["status"] == "detectable"
    assert saved.meta_json["options"]["repre_guard"]["segments"][0]["token_count"] == 259


@pytest.mark.anyio
async def test_pdf_endpoint_projects_saved_headings_before_rendering(monkeypatch):
    record = saved_record("Heading\nBody.", "<h2>Heading</h2><p>Body.</p>")
    before = deepcopy(record.meta_json)
    captured = []
    monkeypatch.setattr(reports.HistoryService, "get_history", lambda *args, **kwargs: record)
    monkeypatch.setattr(reports, "build_report_pdf", lambda payload, user: captured.append(payload) or b"%PDF")
    await reports.export_pdf_report(ReportPdfRequest(history_id=490), db=None, current_user=SimpleNamespace(id=1))
    assert captured[0].analysis.sentences[0].type == "metadata"
    assert captured[0].analysis.summary.ai == 23
    assert record.meta_json == before


@pytest.mark.parametrize("locale,title_label,author_label,paragraph_label", [
    ("zh-CN", "标题", "作者", "段落"), ("en-US", "Title", "Author", "Paragraph"),
])
@pytest.mark.parametrize("byline", ["作者: 朱自清", "作者：朱自清", "Author: John Smith", "AUTHOR：J. R. R. Tolkien"])
def test_pdf_metadata_has_no_ai_human_label_or_percentage(locale, title_label, author_label, paragraph_label, byline):
    record = saved_record(f"背影\n{byline}\n我与父亲不相见已二年余了。")
    analysis = project_document_analysis(record.meta_json["analysis"], record.input_text)
    payload = ReportPdfContent(input_text=record.input_text, analysis=analysis, include_evidence=False, locale=locale)
    before = deepcopy(payload.model_dump())
    data = build_report_pdf(payload, SimpleNamespace(name="reader", email="test@example.invalid"))
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    assert f"{title_label}\n背影\n{author_label}\n{byline}\n{paragraph_label} 1 ·" in text
    assert "仅作上下文" not in text and "context only" not in text
    assert f"{paragraph_label} 2" not in text and f"{paragraph_label} 3" not in text
    assert text.count("AI 生成可能性" if locale == "zh-CN" else "AI generation likelihood") == 1
    assert "23%" in text and "77%" in text
    assert payload.model_dump() == before
