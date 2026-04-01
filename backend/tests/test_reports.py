from io import BytesIO

import pytest
from fastapi import HTTPException
from pypdf import PdfReader

from app.api.v1.auth import register_user
from app.api.v1.reports import export_pdf_report
from app.schemas import ReportPdfRequest
from app.schemas.auth import RegisterRequest
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
async def test_export_pdf_report_prefers_profile_name(db_session, unique_email):
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
    assert f'attachment; filename="aidetector-report-scan-{history.id}-' in response.headers["Content-Disposition"]

    text = _extract_text(response.body)
    assert "AI Detection Report" in text
    assert "Ada Lovelace" in text
    assert "account-name" in text
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
    assert "History Export" in text


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
