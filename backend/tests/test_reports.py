from io import BytesIO

import pytest
from pypdf import PdfReader

from app.api.v1.auth import register_user
from app.api.v1.reports import export_pdf_report
from app.schemas import ReportPdfRequest
from app.schemas.auth import RegisterRequest


def _build_payload(**overrides) -> ReportPdfRequest:
    payload = {
        "report_type": "scan",
        "generated_at": "2026-03-20T10:00:00Z",
        "locale": "en-US",
        "functions": ["scan"],
        "input_text": "This is the original text.\nIt has two lines.",
        "analysis": {
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
        },
    }
    payload.update(overrides)
    return ReportPdfRequest.model_validate(payload)


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.mark.anyio
async def test_export_pdf_report_prefers_profile_name(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="account-name"), db_session)
    user.first_name = "Ada"
    user.surname = "Lovelace"
    db_session.commit()
    db_session.refresh(user)

    response = await export_pdf_report(_build_payload(), current_user=user)

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert 'attachment; filename="aidetector-report-' in response.headers["Content-Disposition"]

    text = _extract_text(response.body)
    assert "AI Detection Report" in text
    assert "Ada Lovelace" in text
    assert "account-name" in text


@pytest.mark.anyio
async def test_export_pdf_report_falls_back_to_account_name(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23", name="fallback-user"), db_session)

    response = await export_pdf_report(
        _build_payload(report_type="history", history_id=42, detection_id=42),
        current_user=user,
    )

    assert response.media_type == "application/pdf"
    assert response.body.startswith(b"%PDF")
    assert 'history-42' in response.headers["Content-Disposition"]

    text = _extract_text(response.body)
    assert "fallback-user" in text
    assert "History Export" in text
