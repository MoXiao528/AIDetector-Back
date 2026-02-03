from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.roles import UserRole, has_required_role, normalize_role
from app.core.security import hash_api_key
from app.db.session import get_db
from app.models.api_key import APIKey, APIKeyStatus
from app.models.user import User
from app.schemas import TokenPayload

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str | None, Depends(oauth2_scheme)]
# FastAPI requires the default to be defined outside of Annotated when using Header
APIKeyHeaderDep = Annotated[str | None, Header(alias="X-API-Key")]


def get_current_user(
    db: SessionDep, token: TokenDep, api_key_header: APIKeyHeaderDep = None
) -> User:
    if api_key_header:
        key_hash = hash_api_key(api_key_header)
        api_key = db.scalar(
            select(APIKey).where(APIKey.key_hash == key_hash, APIKey.status == APIKeyStatus.ACTIVE)
        )
        if api_key is None or api_key.user is None or not api_key.user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "API-Key"},
            )

        api_key.last_used_at = datetime.now(timezone.utc)
        db.add(api_key)
        db.commit()
        return api_key.user

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        token_data = TokenPayload(**payload)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (jwt.InvalidTokenError, ValidationError) as exc:  # pragma: no cover - JWT 库内部异常
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if token_data.sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(token_data.sub)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 非数字 sub
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive or invalid user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


@dataclass
class ActorContext:
    actor_type: str
    actor_id: str
    user: User | None = None


def _decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (jwt.InvalidTokenError, ValidationError) as exc:  # pragma: no cover - JWT 库内部异常
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_actor(db: SessionDep, token: TokenDep) -> ActorContext:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "GUEST_TOKEN_REQUIRED",
                "message": "Authorization token required",
                "detail": "Please call /auth/guest to obtain a token.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = _decode_token(token)
    if token_data.sub is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    actor_type = (token_data.sub_type or "user").lower()
    if actor_type == "guest":
        guest_id = token_data.guest_id or token_data.sub
        if not guest_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return ActorContext(actor_type="guest", actor_id=str(guest_id))

    try:
        user_id = int(token_data.sub)
    except (TypeError, ValueError) as exc:  # pragma: no cover - 非数字 sub
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive or invalid user",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return ActorContext(actor_type="user", actor_id=str(user.id), user=user)


CurrentActorDep = Annotated[ActorContext, Depends(get_current_actor)]


def require_roles(allowed_roles: list[UserRole]):
    """基于角色的依赖封装。"""

    def _checker(current_user: CurrentUserDep) -> User:
        try:
            user_role = normalize_role(current_user.role)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid role",
            ) from exc

        if not has_required_role(user_role, allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return current_user

    return Depends(_checker)


# 常用角色依赖别名，便于复用
ActiveMemberDep = Annotated[User, require_roles([UserRole.INDIVIDUAL, UserRole.TEAM_ADMIN, UserRole.SYS_ADMIN])]
SysAdminDep = Annotated[User, require_roles([UserRole.SYS_ADMIN])]
