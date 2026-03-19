from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status

from app.core.roles import UserRole
from app.db.deps import SessionDep, SysAdminDep
from app.schemas import ErrorResponse
from app.schemas.admin import (
    AdminDetectionDetailResponse,
    AdminDetectionListItem,
    AdminDetectionListResponse,
    AdminDetectionMini,
    AdminOverviewPeriod,
    AdminOverviewPreset,
    AdminOverviewResponse,
    AdminOverviewSeriesItem,
    AdminOverviewSummary,
    AdminRecentDetectionItem,
    AdminRecentUserItem,
    AdminStatusResponse,
    AdminUserCreditsAdjustRequest,
    AdminUserCreditsAdjustResponse,
    AdminUserDetailResponse,
    AdminUserListItem,
    AdminUserListResponse,
    AdminUserProfile,
    AdminUserUpdateRequest,
)
from app.schemas.history import Analysis
from app.services.admin_service import AdminOverviewData, AdminService, DetectionWithUser

router = APIRouter(prefix="/admin", tags=["admin"])


def _build_profile(user) -> AdminUserProfile:
    return AdminUserProfile(
        first_name=user.first_name,
        surname=user.surname,
        organization=user.organization,
        job_role=user.job_role,
        industry=user.industry,
    )


def _build_recent_detection_item(row: DetectionWithUser) -> AdminRecentDetectionItem:
    detection = row.detection
    user = row.user
    return AdminRecentDetectionItem(
        id=detection.id,
        user_id=detection.user_id,
        user_email=user.email if user else None,
        user_name=user.name if user else None,
        actor_type=detection.actor_type,
        actor_id=detection.actor_id,
        label=detection.result_label,
        score=detection.score,
        chars_used=detection.chars_used,
        created_at=detection.created_at,
    )


def _build_detection_mini(detection) -> AdminDetectionMini:
    return AdminDetectionMini(
        id=detection.id,
        label=detection.result_label,
        score=detection.score,
        chars_used=detection.chars_used,
        created_at=detection.created_at,
    )


def _build_detection_list_item(row: DetectionWithUser) -> AdminDetectionListItem:
    detection = row.detection
    user = row.user
    return AdminDetectionListItem(
        id=detection.id,
        user_id=detection.user_id,
        user_email=user.email if user else None,
        user_name=user.name if user else None,
        actor_type=detection.actor_type,
        actor_id=detection.actor_id,
        label=detection.result_label,
        score=detection.score,
        chars_used=detection.chars_used,
        functions_used=detection.functions_used or [],
        created_at=detection.created_at,
    )


def _extract_analysis(detection) -> Analysis | None:
    if not detection.meta_json or "analysis" not in detection.meta_json:
        return None
    try:
        return Analysis(**detection.meta_json["analysis"])
    except Exception:
        return None


def _build_user_list_item(user) -> AdminUserListItem:
    return AdminUserListItem(
        id=user.id,
        email=user.email,
        name=user.name,
        system_role=user.role,
        plan_tier=user.plan_tier,
        is_active=user.is_active,
        credits_total=user.credits_total,
        credits_used=user.credits_used,
        credits_remaining=user.credits,
        created_at=user.created_at,
    )


def _build_user_detail_response(user, recent_detections) -> AdminUserDetailResponse:
    base = _build_user_list_item(user)
    recent_items = [_build_detection_mini(detection) for detection in recent_detections]
    return AdminUserDetailResponse(
        **base.model_dump(),
        profile=_build_profile(user),
        recent_detections=recent_items,
    )


def _build_overview_response(data: AdminOverviewData) -> AdminOverviewResponse:
    return AdminOverviewResponse(
        period=AdminOverviewPeriod(**data.period),
        summary=AdminOverviewSummary(**data.summary),
        series=[AdminOverviewSeriesItem(**item) for item in data.series],
        recent_users=[
            AdminRecentUserItem(
                id=user.id,
                email=user.email,
                name=user.name,
                system_role=user.role,
                is_active=user.is_active,
                created_at=user.created_at,
            )
            for user in data.recent_users
        ],
        recent_detections=[_build_recent_detection_item(item) for item in data.recent_detections],
    )


@router.get(
    "/status",
    response_model=AdminStatusResponse,
    summary="Admin health check",
    status_code=status.HTTP_200_OK,
    responses={403: {"model": ErrorResponse}},
)
async def admin_status(_: SysAdminDep) -> AdminStatusResponse:
    return AdminStatusResponse(message="admin ok")


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    summary="Get admin overview",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def get_admin_overview(
    db: SessionDep,
    _: SysAdminDep,
    preset: AdminOverviewPreset = Query(AdminOverviewPreset.WEEK),
) -> AdminOverviewResponse:
    service = AdminService(db)
    return _build_overview_response(service.get_overview(preset=preset.value))


@router.get(
    "/users",
    response_model=AdminUserListResponse,
    summary="List users for admin",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_admin_users(
    db: SessionDep,
    _: SysAdminDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    search: str | None = Query(None),
    system_role: UserRole | None = Query(None, alias="systemRole"),
    is_active: bool | None = Query(None, alias="isActive"),
    plan_tier: str | None = Query(None, alias="planTier"),
    sort: str = Query("createdAt"),
    order: str = Query("desc"),
) -> AdminUserListResponse:
    service = AdminService(db)
    users, total = service.list_users(
        page=page,
        page_size=page_size,
        search=search,
        system_role=system_role,
        is_active=is_active,
        plan_tier=plan_tier,
        sort=sort,
        order=order,
    )
    return AdminUserListResponse(
        items=[_build_user_list_item(user) for user in users],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetailResponse,
    summary="Get user detail for admin",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_admin_user(
    user_id: int,
    db: SessionDep,
    _: SysAdminDep,
) -> AdminUserDetailResponse:
    service = AdminService(db)
    user = service.get_user(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ADMIN_USER_NOT_FOUND", "message": "User not found", "detail": {"userId": user_id}},
        )
    return _build_user_detail_response(user, service.get_user_recent_detections(user_id))


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserDetailResponse,
    summary="Update admin-managed user fields",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def update_admin_user(
    user_id: int,
    payload: AdminUserUpdateRequest,
    db: SessionDep,
    _: SysAdminDep,
) -> AdminUserDetailResponse:
    service = AdminService(db)
    user = service.update_user(
        user_id,
        system_role=payload.system_role,
        plan_tier=payload.plan_tier,
        is_active=payload.is_active,
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ADMIN_USER_NOT_FOUND", "message": "User not found", "detail": {"userId": user_id}},
        )
    return _build_user_detail_response(user, service.get_user_recent_detections(user_id))


@router.post(
    "/users/{user_id}/credits",
    response_model=AdminUserCreditsAdjustResponse,
    summary="Adjust user credits",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def adjust_admin_user_credits(
    user_id: int,
    payload: AdminUserCreditsAdjustRequest,
    db: SessionDep,
    _: SysAdminDep,
) -> AdminUserCreditsAdjustResponse:
    service = AdminService(db)
    user = service.adjust_user_credits(user_id, delta=payload.delta, reason=payload.reason)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ADMIN_USER_NOT_FOUND", "message": "User not found", "detail": {"userId": user_id}},
        )
    return AdminUserCreditsAdjustResponse(
        user_id=user.id,
        credits_total=user.credits_total,
        credits_used=user.credits_used,
        credits_remaining=user.credits,
    )


@router.get(
    "/detections",
    response_model=AdminDetectionListResponse,
    summary="List detections for admin",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_admin_detections(
    db: SessionDep,
    _: SysAdminDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    search: str | None = Query(None),
    user_id: int | None = Query(None, alias="userId"),
    actor_type: str | None = Query(None, alias="actorType"),
    label: str | None = Query(None),
    function_name: str | None = Query(None, alias="function"),
    date_from: datetime | None = Query(None, alias="dateFrom"),
    date_to: datetime | None = Query(None, alias="dateTo"),
    sort: str = Query("createdAt"),
    order: str = Query("desc"),
) -> AdminDetectionListResponse:
    service = AdminService(db)
    items, total = service.list_detections(
        page=page,
        page_size=page_size,
        search=search,
        user_id=user_id,
        actor_type=actor_type,
        label=label,
        function_name=function_name,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        order=order,
    )
    return AdminDetectionListResponse(
        items=[_build_detection_list_item(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/detections/{detection_id}",
    response_model=AdminDetectionDetailResponse,
    summary="Get detection detail for admin",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_admin_detection(
    detection_id: int,
    db: SessionDep,
    _: SysAdminDep,
) -> AdminDetectionDetailResponse:
    service = AdminService(db)
    item = service.get_detection(detection_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ADMIN_DETECTION_NOT_FOUND",
                "message": "Detection not found",
                "detail": {"detectionId": detection_id},
            },
        )
    base = _build_detection_list_item(item)
    return AdminDetectionDetailResponse(
        **base.model_dump(),
        title=item.detection.title,
        input_text=item.detection.input_text,
        editor_html=item.detection.editor_html,
        meta_json=item.detection.meta_json,
        analysis=_extract_analysis(item.detection),
    )


@router.delete(
    "/detections/{detection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete detection record",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_admin_detection(
    detection_id: int,
    db: SessionDep,
    _: SysAdminDep,
) -> None:
    service = AdminService(db)
    success = service.delete_detection(detection_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ADMIN_DETECTION_NOT_FOUND",
                "message": "Detection not found",
                "detail": {"detectionId": detection_id},
            },
        )
