from fastapi import APIRouter, Response, status

from app.db.deps import ActiveMemberDep
from app.schemas import ReportPdfRequest
from app.services.report_pdf import build_report_filename, build_report_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/pdf",
    summary="Export a PDF report",
    status_code=status.HTTP_200_OK,
)
async def export_pdf_report(
    payload: ReportPdfRequest,
    current_user: ActiveMemberDep,
) -> Response:
    pdf_bytes = build_report_pdf(payload, current_user)
    filename = build_report_filename(payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
