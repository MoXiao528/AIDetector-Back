"""History API endpoints."""

from fastapi import APIRouter, HTTPException, Query, status

from app.db.deps import ActiveMemberDep, SessionDep, _decode_token
from app.schemas.history import (
    Analysis,
    BatchDeleteRequest,
    BatchDeleteResponse,
    ClaimGuestHistoryRequest,
    ClaimGuestHistoryResponse,
    ClearAllResponse,
    HistoryListResponse,
    HistoryRecordCreate,
    HistoryRecordResponse,
    HistoryRecordUpdate,
)
from app.services.history_service import HistoryService

router = APIRouter(prefix="/history", tags=["history"])


def _detection_to_history_response(detection) -> HistoryRecordResponse:
    analysis_data = None
    if detection.meta_json and "analysis" in detection.meta_json:
        try:
            analysis_data = Analysis(**detection.meta_json["analysis"])
        except Exception:
            analysis_data = None

    return HistoryRecordResponse(
        id=detection.id,
        user_id=detection.user_id,
        title=detection.title,
        created_at=detection.created_at,
        functions=detection.functions_used or [],
        input_text=detection.input_text,
        editor_html=detection.editor_html,
        analysis=analysis_data,
    )


def _resolve_guest_id_from_token(guest_token: str) -> str:
    token_data = _decode_token(guest_token)
    actor_type = (token_data.sub_type or "user").lower()
    if actor_type != "guest":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_GUEST_TOKEN", "message": "Provided token is not a guest token."},
        )

    guest_id = token_data.guest_id or token_data.sub
    if not guest_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_GUEST_TOKEN", "message": "Guest token is missing guest_id."},
        )
    return str(guest_id)


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="List history records",
)
async def list_histories(
    db: SessionDep,
    current_user: ActiveMemberDep,
    page: int = Query(1, ge=1, description="Page number, default 1"),
    per_page: int = Query(20, ge=1, le=100, description="Page size, default 20, max 100"),
    sort: str = Query("created_at", description="Sort field, default created_at"),
    order: str = Query("desc", description="Sort order, asc or desc, default desc"),
) -> HistoryListResponse:
    service = HistoryService(db)
    records, total, total_pages = service.list_histories(
        user_id=current_user.id,
        page=page,
        per_page=per_page,
        sort=sort,
        order=order,
    )

    return HistoryListResponse(
        items=[_detection_to_history_response(record) for record in records],
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@router.get(
    "/{history_id}",
    response_model=HistoryRecordResponse,
    summary="Get one history record",
)
async def get_history(
    history_id: int,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    service = HistoryService(db)
    detection = service.get_history(user_id=current_user.id, history_id=history_id)

    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "History record not found."},
        )

    return _detection_to_history_response(detection)


@router.post(
    "",
    response_model=HistoryRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a history record",
)
async def create_history(
    payload: HistoryRecordCreate,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    if not payload.input_text or not payload.input_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_HISTORY_DATA", "message": "Input text cannot be empty."},
        )

    if len(payload.input_text) > 50000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_HISTORY_DATA", "message": "Input text exceeds the 50,000 character limit."},
        )

    service = HistoryService(db)
    analysis_dict = payload.analysis.model_dump() if payload.analysis else None

    try:
        detection = service.create_history(
            user_id=current_user.id,
            title=payload.title,
            functions=payload.functions,
            input_text=payload.input_text,
            editor_html=payload.editor_html,
            analysis=analysis_dict,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "INVALID_HISTORY_DATA", "message": str(exc)},
        ) from exc

    return _detection_to_history_response(detection)


@router.post(
    "/claim-guest",
    response_model=ClaimGuestHistoryResponse,
    summary="Claim guest history records",
)
async def claim_guest_history(
    payload: ClaimGuestHistoryRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> ClaimGuestHistoryResponse:
    guest_id = _resolve_guest_id_from_token(payload.guest_token)
    service = HistoryService(db)
    claimed_count = service.claim_guest_histories(user_id=current_user.id, guest_id=guest_id)
    return ClaimGuestHistoryResponse(claimed_count=claimed_count)


@router.patch(
    "/{history_id}",
    response_model=HistoryRecordResponse,
    summary="Update a history record",
)
async def update_history(
    history_id: int,
    payload: HistoryRecordUpdate,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> HistoryRecordResponse:
    service = HistoryService(db)
    detection = service.update_history(
        user_id=current_user.id,
        history_id=history_id,
        title=payload.title,
    )

    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "History record not found."},
        )

    return _detection_to_history_response(detection)


@router.delete(
    "/{history_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a history record",
)
async def delete_history(
    history_id: int,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> None:
    service = HistoryService(db)
    success = service.delete_history(user_id=current_user.id, history_id=history_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "HISTORY_NOT_FOUND", "message": "History record not found."},
        )


@router.post(
    "/batch-delete",
    response_model=BatchDeleteResponse,
    summary="Batch delete history records",
)
async def batch_delete_histories(
    payload: BatchDeleteRequest,
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> BatchDeleteResponse:
    service = HistoryService(db)
    deleted_count, failed_ids = service.batch_delete_histories(
        user_id=current_user.id,
        ids=payload.ids,
    )

    return BatchDeleteResponse(
        deleted_count=deleted_count,
        failed_ids=failed_ids,
    )


@router.delete(
    "",
    response_model=ClearAllResponse,
    summary="Clear all history records",
)
async def clear_all_histories(
    db: SessionDep,
    current_user: ActiveMemberDep,
) -> ClearAllResponse:
    service = HistoryService(db)
    deleted_count = service.clear_all_histories(user_id=current_user.id)
    return ClearAllResponse(deleted_count=deleted_count)
