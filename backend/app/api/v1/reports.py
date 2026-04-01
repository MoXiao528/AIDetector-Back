from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, status

from app.db.deps import ActiveMemberDep, SessionDep
from app.schemas import ReportPdfContent, ReportPdfRequest
from app.schemas.history import Analysis
from app.services.history_service import HistoryService
from app.services.report_pdf import build_report_filename, build_report_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/pdf",
    summary="Export a PDF report",
    status_code=status.HTTP_200_OK,
)
async def export_pdf_report(
    payload: ReportPdfRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> Response:
    service = HistoryService(db)
    detection = service.get_history(user_id=current_user.id, history_id=payload.history_id)
    if detection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "HISTORY_NOT_FOUND", "message": "History record not found", "detail": payload.history_id},
        )

    analysis_raw = (detection.meta_json or {}).get("analysis")
    if not isinstance(analysis_raw, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "REPORT_SOURCE_INVALID", "message": "History analysis is unavailable", "detail": detection.id},
        )

    try:
        analysis = Analysis.model_validate(analysis_raw)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "REPORT_SOURCE_INVALID", "message": "History analysis is invalid", "detail": detection.id},
        ) from exc

    report_content = ReportPdfContent(
        report_type=payload.report_type,
        generated_at=datetime.now(timezone.utc),
        locale=payload.locale,
        history_id=detection.id,
        detection_id=detection.id,
        functions=detection.functions_used or ["scan"],
        input_text=detection.input_text,
        analysis=analysis,
    )
    pdf_bytes = build_report_pdf(report_content, current_user)
    filename = build_report_filename(report_content)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
