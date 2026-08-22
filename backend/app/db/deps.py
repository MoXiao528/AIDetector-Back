from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Cookie, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.roles import UserRole, has_required_role, normalize_role
from app.core.security import JWT_AUDIENCE, JWT_ISSUER, hash_api_key
from app.db.session import get_db
from app.models.api_key import API_KEY_SCOPES, APIKey, APIKeyStatus
from app.models.guest_session import GuestSession
from app.models.revoked_access_token import RevokedAccessToken
from app.models.user import User
from app.schemas import TokenPayload

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
AUTH_COOKIE_NAME = "aid_access_token"

SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str | None, Depends(oauth2_scheme)]
AuthCookieDep = Annotated[str | None, Cookie(alias=AUTH_COOKIE_NAME)]
# FastAPI requires the default to be defined outside of Annotated when using Header
APIKeyHeaderDep = Annotated[str | None, Header(alias="X-API-Key")]


def _ambiguous_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "AMBIGUOUS_CREDENTIALS",
            "message": "Ambiguous credentials",
            "detail": "Use either a token session or X-API-Key, not both.",
        },
    )


def get_current_user(
    db: SessionDep,
    token: TokenDep,
    auth_cookie: AuthCookieDep = None,
    api_key_header: APIKeyHeaderDep = None,
) -> User:
    if api_key_header and (token or auth_cookie):
        raise _ambiguous_credentials_error()

    if api_key_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "API_KEY_SCOPE_FORBIDDEN",
                "message": "API key scope forbidden",
                "detail": "This endpoint requires an interactive user session.",
            },
        )

    resolved_token = token or auth_cookie
    if resolved_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = _decode_token(resolved_token)
    if token_data.sub_type != "user":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _resolve_active_user(db, token_data)


CurrentUserDep = Annotated[User, Depends(get_current_user)]


@dataclass
class ActorContext:
    actor_type: str
    actor_id: str
    user: User | None = None
    auth_method: Literal["token", "api_key"] = "token"
    scopes: frozenset[str] = field(default_factory=frozenset)
    api_key_id: int | None = None


def _decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["sub", "sub_type", "iss", "aud", "iat", "exp", "jti"]},
        )
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


def _resolve_active_user(db: Session, token_data: TokenPayload) -> User:
    if db.get(RevokedAccessToken, str(token_data.jti)) is not None:
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


def _get_active_guest_session_id(db: Session, session_id: str, *, lock: bool = False) -> str:
    statement = select(GuestSession.id).where(
        GuestSession.id == session_id,
        GuestSession.revoked_at.is_(None),
        GuestSession.expires_at > datetime.now(timezone.utc),
    )
    if lock:
        statement = statement.with_for_update()

    active_session_id = db.scalar(statement)
    if active_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return active_session_id


def _resolve_active_guest_session_id(db: Session, token_data: TokenPayload) -> str:
    session_id = token_data.sid
    if token_data.sub_type != "guest" or not session_id or token_data.sub != session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _get_active_guest_session_id(db, session_id)


def _resolve_active_api_key(db: Session, raw_key: str) -> APIKey:
    now = datetime.now(timezone.utc)
    api_key = db.scalar(
        select(APIKey).where(
            APIKey.key_hash == hash_api_key(raw_key),
            APIKey.status == APIKeyStatus.ACTIVE,
            APIKey.revoked_at.is_(None),
            APIKey.expires_at > now,
        )
    )
    if api_key is None or api_key.user is None or not api_key.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_API_KEY",
                "message": "Invalid API key",
                "detail": "The API key is invalid, expired, or revoked.",
            },
            headers={"WWW-Authenticate": "API-Key"},
        )

    api_key.last_used_at = now
    db.add(api_key)
    db.commit()
    return api_key


def get_current_actor(
    db: SessionDep,
    token: TokenDep,
    auth_cookie: AuthCookieDep = None,
    api_key_header: APIKeyHeaderDep = None,
) -> ActorContext:
    resolved_token = token or auth_cookie
    if resolved_token is None:
        if api_key_header:
            api_key = _resolve_active_api_key(db, api_key_header)
            return ActorContext(
                actor_type="user",
                actor_id=str(api_key.user.id),
                user=api_key.user,
                auth_method="api_key",
                scopes=frozenset(API_KEY_SCOPES),
                api_key_id=api_key.id,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "GUEST_TOKEN_REQUIRED",
                "message": "Authorization token required",
                "detail": "Please call /auth/guest to obtain a token.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = _decode_token(resolved_token)
    if api_key_header:
        raise _ambiguous_credentials_error()

    actor_type = token_data.sub_type
    if actor_type == "guest":
        session_id = _resolve_active_guest_session_id(db, token_data)
        return ActorContext(actor_type="guest", actor_id=session_id)

    if actor_type != "user":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = _resolve_active_user(db, token_data)

    return ActorContext(actor_type="user", actor_id=str(user.id), user=user)


AuthenticatedActorDep = Annotated[ActorContext, Depends(get_current_actor)]


def require_session_actor(current_actor: AuthenticatedActorDep) -> ActorContext:
    if current_actor.auth_method == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "API_KEY_SCOPE_FORBIDDEN",
                "message": "API key scope forbidden",
                "detail": "This endpoint requires a token session.",
            },
        )
    return current_actor


def require_actor_scope(required_scope: str):
    def _checker(current_actor: AuthenticatedActorDep) -> ActorContext:
        if current_actor.auth_method == "api_key" and required_scope not in current_actor.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "API_KEY_SCOPE_FORBIDDEN",
                    "message": "API key scope forbidden",
                    "detail": f"The API key requires the {required_scope} scope.",
                },
            )
        return current_actor

    return Depends(_checker)


def require_api_key_actor(current_actor: AuthenticatedActorDep) -> ActorContext:
    if current_actor.auth_method != "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "API_KEY_REQUIRED",
                "message": "API key required",
                "detail": "Authenticate this endpoint with X-API-Key.",
            },
        )
    return current_actor


CurrentActorDep = Annotated[ActorContext, Depends(require_session_actor)]
DetectActorDep = Annotated[ActorContext, require_actor_scope("detect:write")]
QuotaActorDep = Annotated[ActorContext, require_actor_scope("quota:read")]
APIKeyActorDep = Annotated[ActorContext, Depends(require_api_key_actor)]


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
