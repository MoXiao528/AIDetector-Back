from fastapi import APIRouter, status

from app.db.deps import SysAdminDep
from app.schemas import AdminStatusResponse, ErrorResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/status",
    response_model=AdminStatusResponse,
    summary="系统管理员健康检查",
    status_code=status.HTTP_200_OK,
    responses={403: {"model": ErrorResponse}},
)
async def admin_status(_: SysAdminDep) -> AdminStatusResponse:
    return AdminStatusResponse(message="admin ok")
