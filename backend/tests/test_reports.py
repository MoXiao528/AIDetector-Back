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
from app.api.v1.reports import export_pdf_report
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.schemas import ReportPdfRequest
from app.schemas.auth import RegisterRequest
from app.services.evidence_engine import METRICS
from app.services.history_service import HistoryService


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
@pytest.mark.parametrize(
    "mode,include_evidence,expected",
    [("off", True, False), ("shadow", True, False), ("serve", False, False), ("serve", True, True)],
)
async def test_report_uses_saved_snapshot_and_evidence_gates(
    db_session, unique_email, monkeypatch, mode, include_evidence, expected
):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
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
    assert content.include_evidence is expected
    assert (content.evidence is not None) is expected
    if expected:
        assert content.evidence.model_dump() == original_meta["evidence"]
    db_session.refresh(history)
    assert history.meta_json == original_meta


@pytest.mark.anyio
@pytest.mark.parametrize("stored_evidence", [None, {}, {"status": "not-valid"}])
async def test_report_tolerates_missing_or_invalid_evidence(db_session, unique_email, monkeypatch, stored_evidence):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
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
