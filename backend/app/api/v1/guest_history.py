"""History management restricted to the current, unclaimed guest session."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.v1.history import _detection_to_history_response
from app.db.deps import (
    APIKeyHeaderDep,
    AuthCookieDep,
    SessionDep,
    TokenDep,
    _ambiguous_credentials_error,
    _decode_token,
    _get_active_guest_session_id,
    _resolve_active_guest_session_id,
)
from app.schemas.history import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    ClearAllResponse,
    HistoryListResponse,
    HistoryRecordResponse,
    HistoryRecordUpdate,
)
from app.services.history_service import HistoryService

router = APIRouter(prefix="/guest/history", tags=["history"])


def _guest_history_service(
    request: Request,
    db: SessionDep,
    token: TokenDep,
    auth_cookie: AuthCookieDep = None,
    api_key_header: APIKeyHeaderDep = None,
) -> HistoryService:
    if api_key_header:
        if token or auth_cookie:
            raise _ambiguous_credentials_error()
        raise HTTPException(status_code=403, detail="A guest Bearer session is required.")
    if not token:
        raise HTTPException(status_code=401, detail="A guest Bearer session is required.", headers={"WWW-Authenticate": "Bearer"})
    guest_id = _resolve_active_guest_session_id(db, _decode_token(token))
    if request.method != "GET":
        # Claim and discard take this same row lock before consuming the capability.
        _get_active_guest_session_id(db, guest_id, lock=True)
    return HistoryService(db, guest_id=guest_id)


GuestHistoryServiceDep = Annotated[HistoryService, Depends(_guest_history_service)]


def _require_record(record):
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "HISTORY_NOT_FOUND", "message": "History record not found."})
    return _detection_to_history_response(record)


@router.get("", response_model=HistoryListResponse, summary="List current guest history records")
def list_guest_histories(
    service: GuestHistoryServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: str = "created_at",
    order: str = "desc",
    q: Annotated[str | None, Query(max_length=200)] = None,
    pinned: bool | None = None,
) -> HistoryListResponse:
    records, total, total_pages = service.list_histories(None, page, per_page, sort, order, q, pinned)
    return HistoryListResponse(
        items=[_detection_to_history_response(record) for record in records],
        total=total, page=page, per_page=per_page, total_pages=total_pages,
    )


@router.get("/{history_id}", response_model=HistoryRecordResponse, summary="Get a current guest history record")
def get_guest_history(history_id: int, service: GuestHistoryServiceDep) -> HistoryRecordResponse:
    return _require_record(service.get_history(None, history_id))


@router.patch("/{history_id}", response_model=HistoryRecordResponse, summary="Update a current guest history record")
def update_guest_history(
    history_id: int, payload: HistoryRecordUpdate, service: GuestHistoryServiceDep,
) -> HistoryRecordResponse:
    if payload.title is None and payload.is_pinned is None:
        raise HTTPException(status_code=400, detail={"error": "INVALID_HISTORY_DATA", "message": "No history fields were provided."})
    return _require_record(service.update_history(None, history_id, payload.title, payload.is_pinned))


@router.delete("/{history_id}", status_code=204, summary="Delete a current guest history record")
def delete_guest_history(history_id: int, service: GuestHistoryServiceDep) -> None:
    if not service.delete_history(None, history_id):
        raise HTTPException(status_code=404, detail={"error": "HISTORY_NOT_FOUND", "message": "History record not found."})


@router.post("/batch-delete", response_model=BatchDeleteResponse, summary="Batch delete current guest history records")
def batch_delete_guest_histories(payload: BatchDeleteRequest, service: GuestHistoryServiceDep) -> BatchDeleteResponse:
    deleted_count, failed_ids = service.batch_delete_histories(None, payload.ids)
    return BatchDeleteResponse(deleted_count=deleted_count, failed_ids=failed_ids)


@router.delete("", response_model=ClearAllResponse, summary="Clear current guest history records")
def clear_guest_histories(service: GuestHistoryServiceDep) -> ClearAllResponse:
    return ClearAllResponse(deleted_count=service.clear_all_histories(None))
