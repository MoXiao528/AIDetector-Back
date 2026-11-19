import json
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader
from pydantic import ValidationError
import yaml

from app.api.v1 import reports as reports_api
from app.api.v1.auth import register_user
from app.api.v1.history import get_history
from app.api.v1.reports import export_pdf_report
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.schemas import ReportPdfContent, ReportPdfRequest
from app.schemas.auth import RegisterRequest
from app.schemas.evidence import validate_evidence_snapshot
from app.services.evidence_engine import METRICS
from app.services.history_service import HistoryService
from app.services.report_pdf import _visible_corpus_expressions


def _build_analysis() -> dict:
    return {
        "summary": {"ai": 55, "mixed": 20, "human": 25},
        "sentences": [
            {
                "id": "sent-1",
                "text": "This paragraph looks AI-generated.",
                "raw": "This paragraph looks AI-generated.",
                "type": "ai",
                "probability": 0.91,
                "score": 91,
                "reason": "Structured phrasing",
                "suggestion": "",
            },
            {
                "id": "sent-2",
                "text": "This sentence feels human.",
                "raw": "This sentence feels human.",
                "type": "human",
                "probability": 0.18,
                "score": 18,
                "reason": "Natural variation",
                "suggestion": "",
            },
        ],
        "translation": "",
        "polish": "",
        "citations": [],
        "ai_likely_count": 1,
        "highlighted_html": "<p>preview</p>",
    }


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _create_history(db_session, user_id: int, title: str = "History report source"):
    service = HistoryService(db_session)
    return service.create_history(
        user_id=user_id,
        title=title,
        functions=["scan"],
        input_text="This is the original text.\nIt has two lines.",
        editor_html="<p>This is the original text.</p><p>It has two lines.</p>",
        analysis=_build_analysis(),
    )


@pytest.mark.anyio
async def test_export_pdf_report_uses_account_and_saved_record(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="account-name"), db_session)
    user.first_name = "Ada"
    user.surname = "Lovelace"
    db_session.commit()
    db_session.refresh(user)
    history = _create_history(db_session, user.id)

    response = await export_pdf_report(
        ReportPdfRequest(report_type="scan", locale="en-US", history_id=history.id),
        db=db_session,
        current_user=user,
    )

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert f'attachment; filename="repreguard-report-{history.id}-' in response.headers["Content-Disposition"]

    text = _extract_text(response.body)
    assert "AI Text Detection Report" in text
    assert "Ada Lovelace" not in text
    assert "account-name" in text
    assert "History report source" in text
    assert "This is the original text." in text
    assert "Mixed" not in text
    assert "55%" in text
    assert "45%" in text


@pytest.mark.anyio
async def test_export_pdf_report_falls_back_to_account_name(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="fallback-user"), db_session)
    history = _create_history(db_session, user.id)

    response = await export_pdf_report(
        ReportPdfRequest(report_type="history", locale="en-US", history_id=history.id),
        db=db_session,
        current_user=user,
    )

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")

    text = _extract_text(response.body)
    assert "fallback-user" in text
    assert "History report source" in text


@pytest.mark.anyio
async def test_export_pdf_report_rejects_missing_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="missing-history"), db_session)

    with pytest.raises(HTTPException) as exc_info:
        await export_pdf_report(
            ReportPdfRequest(report_type="history", locale="en-US", history_id=999999),
            db=db_session,
            current_user=user,
        )

    assert exc_info.value.status_code == 404


def _evidence_snapshot():
    return {
        "status": "ready",
        "artifactVersion": "1" * 64,
        "featureSchemaVersion": 1,
        "route": {
            "language": "en", "domain": "academic",
            "confidence": {"language": 0.95, "domain": 0.9},
            "lengthBucket": "short", "fallbackLevel": "exact",
        },
        "quality": {"level": "ready", "coverage": 1.0, "reasons": []},
        "signals": [
            {
                "dimension": dimension, "metric": metric, "observed": 0.5,
                "humanPercentile": 50.0, "aiPercentile": 50.0,
                "referenceRanges": {"human": [0.2, 0.6], "ai": [0.4, 0.8]},
                "relation": {"human": "within", "ai": "within"},
                "notice": None, "sampleCount": 20, "offsets": [], "reasons": [],
            }
            for metric, dimension in METRICS.items()
        ],
        "patterns": {"descriptive_top_tokens": [], "repeated_phrases": [], "sentence_start_templates": []},
    }


@pytest.mark.anyio
@pytest.mark.parametrize("include_evidence", [False, True])
async def test_report_uses_saved_snapshot_and_respects_include_evidence(
    db_session, unique_email, monkeypatch, include_evidence
):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    history = _create_history(db_session, user.id, title="Saved report title")
    detected_at = datetime(2026, 11, 1, 3, 20, tzinfo=timezone.utc)
    history.created_at = detected_at
    history.chars_used = 37
    history.meta_json = {
        **history.meta_json,
        "evidence": _evidence_snapshot(),
        "options": {"repre_guard": {"model_name": "saved-model-version"}},
    }
    db_session.commit()
    original_meta = deepcopy(history.meta_json)
    captured = []
    monkeypatch.setattr(reports_api, "build_report_pdf", lambda payload, current_user: captured.append(payload) or b"%PDF-test")

    response = await export_pdf_report(
        ReportPdfRequest(historyId=history.id, locale="en-US", timeZone="Asia/Shanghai", includeEvidence=include_evidence),
        db=db_session, current_user=user,
    )

    assert response.status_code == 200
    content = captured[0]
    assert content.title == history.title
    assert content.detected_at == history.created_at
    assert content.generated_at.replace(tzinfo=None) > detected_at.replace(tzinfo=None)
    assert content.chars_used == 37
    assert content.model_name == "saved-model-version"
    assert content.time_zone == "Asia/Shanghai"
    assert content.input_text == history.input_text
    assert content.analysis.summary.ai == 55
    assert content.analysis.summary.human == 45
    assert [item.probability for item in content.analysis.sentences] == [0.91, 0.18]
    assert content.include_evidence is include_evidence
    assert (content.evidence is not None) is include_evidence
    if include_evidence:
        assert content.evidence.model_dump() == original_meta["evidence"]
    db_session.refresh(history)
    assert history.meta_json == original_meta


CORPUS_EXPRESSIONS = {
    "status": "matched",
    "lexiconVersion": "1.0.0",
    "lexiconSha256": "2" * 64,
    "expressions": ["值得关注的是", "不仅仅是"],
}


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "unknown"},
        {"expressions": ["重复", "重复"]},
        {"expressions": [" "]},
        {"expressions": []},
        {"status": "no_match"},
        {"status": "unavailable"},
        {"lexiconSha256": "F" * 64},
        {"lexiconVersion": None},
        {"lexiconVersion": None, "lexiconSha256": None},
        {"rawSource": "must not leak"},
    ],
)
def test_invalid_corpus_expressions_leave_the_parent_snapshot_intact(updates):
    snapshot = _evidence_snapshot()
    snapshot["route"]["language"] = "zh"
    value = {**snapshot, "corpusExpressions": {**CORPUS_EXPRESSIONS, **updates}}
    before = deepcopy(value)
    assert validate_evidence_snapshot(value).model_dump() == snapshot
    assert value == before


def test_corpus_expressions_are_omitted_without_a_chinese_route():
    snapshot = _evidence_snapshot()
    value = {**snapshot, "corpusExpressions": CORPUS_EXPRESSIONS}
    assert validate_evidence_snapshot(value).model_dump() == snapshot


@pytest.fixture
def corpus_report_case():
    fixture_path = Path(__file__).resolve().parents[2] / "contract/fixtures/corpus-expression-visibility.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    snapshot = _evidence_snapshot()
    snapshot["route"]["language"] = "zh"
    snapshot["status"] = "insufficient"
    snapshot["quality"] = {"level": "insufficient", "coverage": 0.0, "reasons": []}
    for signal in snapshot["signals"]:
        signal.update(relation=None, referenceRanges=None, humanPercentile=None, aiPercentile=None)
    snapshot["corpusExpressions"] = fixture["corpusExpressions"]
    analysis = _build_analysis()
    segment = analysis["sentences"][0]
    analysis["summary"] = fixture["summary"]
    analysis["sentences"] = [
        {**segment, **entry, "id": f"block-{index}", "text": entry["raw"]}
        for index, entry in enumerate(fixture["sentences"])
    ]
    return {
        "input_text": fixture["inputText"], "analysis": analysis, "evidence": snapshot,
    }, fixture["expectedExpressions"]


@pytest.mark.parametrize(
    "case",
    [
        "ai_zero", "human_full", "legacy", "no_match", "no_ai_segment", "human_only_match", "missing_index",
        "overlap", "missing_paragraph", "raw_mismatch", "text_mismatch", "offset_outside", "offset_mismatch",
    ],
)
def test_pdf_corpus_visibility_hides_without_a_reliable_ai_occurrence(corpus_report_case, case):
    content, _ = corpus_report_case
    analysis = content["analysis"]
    corpus = content["evidence"]["corpusExpressions"]
    match = corpus["matches"][-1]
    if case == "ai_zero":
        analysis["summary"]["ai"] = 0
    elif case == "human_full":
        analysis["summary"]["human"] = 100
    elif case == "legacy":
        corpus.pop("matches")
    elif case == "no_match":
        corpus.pop("matches")
        corpus.update(status="no_match", expressions=[])
    elif case == "no_ai_segment":
        analysis["sentences"][2]["type"] = "human"
    elif case == "human_only_match":
        corpus.update(expressions=[match["expression"]], matches=[match])
    elif case == "missing_index":
        analysis["sentences"][2].pop("startParagraph")
    elif case == "overlap":
        analysis["sentences"][2]["startParagraph"] -= 1
    elif case == "missing_paragraph":
        analysis["sentences"].pop()
    elif case == "raw_mismatch":
        analysis["sentences"][0]["raw"] = analysis["sentences"][0]["raw"].strip()
    elif case == "text_mismatch":
        analysis["sentences"][0]["text"] = analysis["sentences"][0]["text"].strip()
    elif case == "offset_outside":
        match["offsets"][0]["end"] = len(content["input_text"]) + 1
    elif case == "offset_mismatch":
        match["offsets"][0]["start"] -= 1
    assert _visible_corpus_expressions(ReportPdfContent(**content)) == []


def test_pdf_corpus_visibility_can_use_text_when_raw_is_empty(corpus_report_case):
    content, expected_expressions = corpus_report_case
    content["analysis"]["sentences"][2]["raw"] = ""
    assert _visible_corpus_expressions(ReportPdfContent(**content)) == expected_expressions


@pytest.mark.anyio
@pytest.mark.parametrize(
    "locale,expected_heading,note",
    [
        ("zh-CN", "疑似 AI 片段中的常见表达", "仅作为用词参考，不参与检测评分"),
        ("en-US", "Common expressions in passages flagged as AI",
         "For wording reference only. These expressions do not affect the detection score."),
    ],
)
async def test_insufficient_evidence_pdf_filters_saved_matches_to_ai_passages(
    db_session, unique_email, locale, expected_heading, note, corpus_report_case
):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="saved-expression-reader"), db_session)
    content, expected_expressions = corpus_report_case
    history = HistoryService(db_session).create_history(
        user_id=user.id, title="Saved expression report", functions=["scan"],
        input_text=content["input_text"], editor_html="", analysis=content["analysis"],
    )
    snapshot = content["evidence"]
    history.meta_json = {**history.meta_json, "evidence": snapshot}
    db_session.commit()

    saved = await get_history(history.id, db=db_session, current_user=user)
    assert saved.evidence.model_dump() == snapshot
    for include_evidence in [True, False]:
        response = await export_pdf_report(
            ReportPdfRequest(historyId=history.id, locale=locale, includeEvidence=include_evidence),
            db=db_session,
            current_user=user,
        )
        report = _extract_text(response.body)
        assert (expected_heading in report) is include_evidence
        assert (note in report) is include_evidence
        if include_evidence:
            rendered = report.split(expected_heading, 1)[1].split(note, 1)[0].strip()
            separator = "、" if locale == "zh-CN" else ", "
            assert rendered == separator.join(expected_expressions)
    db_session.refresh(history)
    assert history.meta_json["evidence"] == snapshot


@pytest.mark.anyio
@pytest.mark.parametrize("stored_evidence", [None, {}, {"status": "not-valid"}])
async def test_report_tolerates_missing_or_invalid_evidence(db_session, unique_email, monkeypatch, stored_evidence):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    history = _create_history(db_session, user.id)
    history.meta_json = {**history.meta_json, "evidence": stored_evidence, "options": {"repre_guard": None}}
    db_session.commit()
    captured = []
    monkeypatch.setattr(reports_api, "build_report_pdf", lambda payload, current_user: captured.append(payload) or b"%PDF-test")

    response = await export_pdf_report(ReportPdfRequest(historyId=history.id), db=db_session, current_user=user)

    assert response.status_code == 200
    assert captured[0].evidence is None
    assert captured[0].include_evidence is True
    assert captured[0].model_name is None
    assert captured[0].analysis.summary.ai == 55


@pytest.mark.anyio
@pytest.mark.parametrize("invalid_meta", [None, [], {}, {"analysis": []}, {"analysis": {"summary": {"ai": 55}}}])
async def test_report_rejects_missing_or_invalid_analysis(db_session, unique_email, invalid_meta):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    history = _create_history(db_session, user.id)
    history.meta_json = invalid_meta
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await export_pdf_report(ReportPdfRequest(historyId=history.id), db=db_session, current_user=user)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "REPORT_SOURCE_INVALID"


@pytest.mark.anyio
async def test_report_http_auth_ownership_and_download_header(db_session, unique_email, monkeypatch):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    other = await register_user(RegisterRequest(email=f"other-{unique_email}", password="StrongPass!23"), db_session)
    history = _create_history(db_session, user.id)
    own_token = create_access_token(str(user.id), extra_claims={"sub_type": "user"})
    other_token = create_access_token(str(other.id), extra_claims={"sub_type": "user"})
    guest_id = str(uuid4())
    guest_token = create_access_token(guest_id, extra_claims={"sub_type": "guest", "sid": guest_id})
    monkeypatch.setattr(reports_api, "build_report_pdf", lambda payload, current_user: b"%PDF-test")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        payload = {"historyId": history.id}
        for headers in ({}, {"Authorization": f"Bearer {guest_token}"}):
            assert client.post("/api/v1/reports/pdf", json=payload, headers=headers).status_code == 401
        response = client.post("/api/v1/reports/pdf", json=payload, headers={"Authorization": f"Bearer {other_token}"})
        assert response.status_code == 404
        origin = str(get_settings().backend_cors_origins[0])
        response = client.post("/api/v1/reports/pdf", json=payload, headers={"Authorization": f"Bearer {own_token}", "Origin": origin})
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "Content-Disposition" in response.headers["access-control-expose-headers"]
        assert "Retry-After" in response.headers["access-control-expose-headers"]
        assert response.headers["content-disposition"].startswith('attachment; filename="repreguard-report-')
    finally:
        app.dependency_overrides.clear()


def test_report_request_options_match_documented_contract():
    default = ReportPdfRequest(historyId=1)
    assert default.include_evidence is True
    assert default.time_zone == "UTC"
    explicit = ReportPdfRequest(historyId=1, includeEvidence=False, timeZone="Asia/Shanghai")
    assert explicit.model_dump(by_alias=True)["includeEvidence"] is False
    assert explicit.model_dump(by_alias=True)["timeZone"] == "Asia/Shanghai"
    with pytest.raises(ValidationError):
        ReportPdfRequest(historyId=1, timeZone="x" * 65)
    with pytest.raises(ValidationError):
        ReportPdfRequest(historyId=1, timeZone="")

    contract = yaml.safe_load((Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml").read_text(encoding="utf-8"))
    properties = contract["components"]["schemas"]["ReportPdfRequest"]["properties"]
    assert properties["includeEvidence"]["default"] is True
    assert properties["timeZone"]["default"] == "UTC"
    assert properties["timeZone"]["maxLength"] == 64
