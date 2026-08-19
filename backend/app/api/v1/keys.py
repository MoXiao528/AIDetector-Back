from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Path, status
from sqlalchemy import func, select

from app.core.security import generate_api_key
from app.db.deps import APIKeyActorDep, ActiveMemberDep, SessionDep
from app.models.api_key import (
    API_KEY_SCOPES,
    API_KEY_TTL_DAYS,
    MAX_ACTIVE_API_KEYS,
    APIKey,
    APIKeyStatus,
)
from app.models.user import User
from app.schemas import (
    APIKeyCreateRequest,
    APIKeyCreateResponse,
    APIKeyResponse,
    APIKeySelfTestResponse,
    ErrorResponse,
)

router = APIRouter(prefix="/keys", tags=["api_keys"])


@router.post(
    "",
    response_model=APIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="创建新的 API Key（仅返回一次明文 key）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_api_key(payload: APIKeyCreateRequest, db: SessionDep, current_user: ActiveMemberDep) -> APIKeyCreateResponse:
    now = datetime.now(timezone.utc)
    db.scalar(select(User.id).where(User.id == current_user.id).with_for_update())
    active_count = db.scalar(
        select(func.count())
        .select_from(APIKey)
        .where(
            APIKey.user_id == current_user.id,
            APIKey.status == APIKeyStatus.ACTIVE,
            APIKey.revoked_at.is_(None),
            APIKey.expires_at > now,
        )
    ) or 0
    if active_count >= MAX_ACTIVE_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "API_KEY_LIMIT_REACHED",
                "message": f"At most {MAX_ACTIVE_API_KEYS} active API keys are allowed",
                "detail": {"limit": MAX_ACTIVE_API_KEYS},
            },
        )

    plain_key, key_hash = generate_api_key()

    existing_key = db.scalar(select(APIKey).where(APIKey.key_hash == key_hash))
    if existing_key:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Key generation conflict, please retry")

    api_key = APIKey(
        user_id=current_user.id,
        name=payload.name,
        key_hash=key_hash,
        expires_at=now + timedelta(days=API_KEY_TTL_DAYS),
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        status=api_key.status,
        scopes=API_KEY_SCOPES,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        expires_at=api_key.expires_at,
        revoked_at=api_key.revoked_at,
        key=plain_key,
    )


@router.get(
    "",
    response_model=list[APIKeyResponse],
    summary="列出当前用户的 API Keys",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_api_keys(current_user: ActiveMemberDep, db: SessionDep) -> list[APIKeyResponse]:
    api_keys = db.scalars(select(APIKey).where(APIKey.user_id == current_user.id).order_by(APIKey.created_at.desc())).all()
    return api_keys


@router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="禁用 API Key（软删除）",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def deactivate_api_key(
    current_user: ActiveMemberDep,
    db: SessionDep,
    key_id: int = Path(..., description="要禁用的 Key ID"),
) -> None:
    api_key = db.get(APIKey, key_id)
    if api_key is None or api_key.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    if api_key.status == APIKeyStatus.ACTIVE:
        api_key.status = APIKeyStatus.INACTIVE
        api_key.revoked_at = datetime.now(timezone.utc)
        db.add(api_key)
        db.commit()


@router.get(
    "/self-test",
    response_model=APIKeySelfTestResponse,
    summary="使用 API Key 自检",  # 用于验收
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def api_key_self_test(_: APIKeyActorDep) -> APIKeySelfTestResponse:
    return APIKeySelfTestResponse(message="API key is valid")
