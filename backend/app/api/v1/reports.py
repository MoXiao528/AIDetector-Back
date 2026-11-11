from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response, status

from app.core.config import get_settings
from app.db.deps import ActiveMemberDep, SessionDep
from app.schemas import ReportPdfContent, ReportPdfRequest
from app.schemas.evidence import project_public_evidence
from app.schemas.history import project_document_analysis
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

    meta = detection.meta_json if isinstance(detection.meta_json, dict) else {}
    analysis_raw = meta.get("analysis")
    if not isinstance(analysis_raw, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "REPORT_SOURCE_INVALID", "message": "History analysis is unavailable", "detail": detection.id},
        )

    try:
        analysis = project_document_analysis(analysis_raw, detection.input_text, detection.editor_html)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "REPORT_SOURCE_INVALID", "message": "History analysis is invalid", "detail": detection.id},
        ) from exc

    include_evidence = payload.include_evidence and get_settings().detect_evidence_mode == "serve"
    options = meta.get("options")
    model = options.get("repre_guard") if isinstance(options, dict) else None
    model_name = model.get("model_name") if isinstance(model, dict) else None

    report_content = ReportPdfContent(
        report_type=payload.report_type,
        generated_at=datetime.now(timezone.utc),
        detected_at=detection.created_at,
        title=detection.title or "",
        chars_used=detection.chars_used,
        model_name=model_name if isinstance(model_name, str) else None,
        locale=payload.locale,
        time_zone=payload.time_zone,
        include_evidence=include_evidence,
        evidence=project_public_evidence(meta.get("evidence")) if include_evidence else None,
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
