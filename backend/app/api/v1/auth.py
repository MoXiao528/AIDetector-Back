from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.core.rate_limit import auth_rate_limiter
from app.core.roles import UserRole
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.deps import CurrentUserDep, SessionDep, TokenDep
from app.models.guest_session import GuestSession
from app.models.user import User
from app.schemas import ErrorResponse, GuestTokenRequest, LoginRequest, RegisterRequest, Token, UserProfileUpdate, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
AUTH_COOKIE_NAME = "aid_access_token"
GUEST_REFRESH_COOKIE_NAME = "aid_guest_refresh"
GUEST_REFRESH_COOKIE_PATH = "/api/v1/auth/guest"
GUEST_SESSION_TTL = timedelta(days=30)
DEVELOPMENT_ENVIRONMENTS = {"development", "dev", "local", "test"}


def _is_secure_cookie() -> bool:
    return str(settings.environment or "").strip().lower() not in DEVELOPMENT_ENVIRONMENTS


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path="/",
    )


def _hash_guest_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_guest_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=GUEST_REFRESH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        max_age=int(GUEST_SESSION_TTL.total_seconds()),
        path=GUEST_REFRESH_COOKIE_PATH,
    )


def _clear_guest_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=GUEST_REFRESH_COOKIE_NAME,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path=GUEST_REFRESH_COOKIE_PATH,
    )


def _invalid_guest_refresh_response(*, clear_cookie: bool = False) -> JSONResponse:
    error = ErrorResponse(
        code="GUEST_SESSION_INVALID",
        message="Guest session is invalid or expired",
        detail="Obtain a new guest session.",
    )
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=error.model_dump(by_alias=True),
    )
    if clear_cookie:
        _clear_guest_refresh_cookie(response)
    return response


def _resolve_client_ip(request: Request | None) -> str:
    if request is None:
        return "direct-call"

    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _enforce_rate_limit(
    request: Request | None,
    action: str,
    *,
    limit: int,
    window_seconds: int,
    identifier: str = "",
) -> None:
    if request is None:
        return

    normalized_identifier = str(identifier or "").strip().lower()
    bucket = f"auth:{action}:{_resolve_client_ip(request)}:{normalized_identifier}"
    allowed, retry_after = auth_rate_limiter.allow(bucket=bucket, limit=limit, window_seconds=window_seconds)
    if allowed:
        return

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "AUTH_RATE_LIMITED",
            "message": "Too many authentication attempts",
            "detail": {
                "action": action,
                "retry_after": retry_after,
            },
        },
        headers={"Retry-After": str(retry_after)},
    )


def _invalid_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "AUTH_INVALID_CREDENTIALS",
            "message": "Invalid credentials",
            "detail": "Invalid credentials",
        },
    )


@router.post(
    "/register",
    response_model=UserResponse,
    summary="用户注册",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def register_user(payload: RegisterRequest, db: SessionDep, request: Request = None) -> UserResponse:
    _enforce_rate_limit(request=request, action="register-ip", limit=5, window_seconds=600)
    name_value = payload.name.strip() if payload.name and payload.name.strip() else payload.email
    existing_user = db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AUTH_EMAIL_EXISTS",
                "message": "Email already registered",
                "detail": "Email already registered",
            },
        )
    existing_name = db.scalar(select(User).where(User.name == name_value))
    if existing_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AUTH_NAME_EXISTS",
                "message": "Name already registered",
                "detail": "Name already registered",
            },
        )

    user = User(
        email=payload.email,
        name=name_value,
        password_hash=get_password_hash(payload.password),
        role=UserRole.INDIVIDUAL,
        plan_tier="personal-free",
        credits_total=30000,
        credits_used=0,
        onboarding_completed=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    summary="用户登录获取 JWT",
    responses={401: {"model": ErrorResponse}},
)
async def login(payload: LoginRequest, response: Response, db: SessionDep, request: Request = None) -> Token:
    _enforce_rate_limit(request=request, action="login-ip", limit=10, window_seconds=300)
    _enforce_rate_limit(request=request, action="login-identifier", limit=5, window_seconds=300, identifier=payload.identifier)
    if "@" in payload.identifier:
        user = db.scalar(select(User).where(User.email == payload.identifier))
    else:
        user = db.scalar(select(User).where(User.name == payload.identifier))
    if user is None:
        raise _invalid_credentials_error()
    if not verify_password(payload.password, user.password_hash):
        raise _invalid_credentials_error()

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        subject=str(user.id),
        expires_delta=access_token_expires,
        extra_claims={"sub_type": "user"},
    )
    _set_auth_cookie(response, access_token)
    return Token(access_token=access_token, token_type="bearer")


@router.post(
    "/guest",
    response_model=Token,
    summary="游客登录获取 JWT",
)
async def guest_login(
    response: Response,
    db: SessionDep,
    token: TokenDep,
    payload: GuestTokenRequest | None = None,
    guest_refresh: Annotated[str | None, Cookie(alias=GUEST_REFRESH_COOKIE_NAME)] = None,
    request: Request = None,
) -> Token | Response:
    _enforce_rate_limit(request=request, action="guest-ip", limit=20, window_seconds=300)

    now = datetime.now(timezone.utc)
    refresh_token = secrets.token_urlsafe(32)
    expires_at = now + GUEST_SESSION_TTL
    refresh_token_hash = _hash_guest_refresh_token(refresh_token)

    if guest_refresh:
        provided_refresh_hash = _hash_guest_refresh_token(guest_refresh)
        guest_id = db.scalar(
            update(GuestSession)
            .where(
                GuestSession.refresh_token_hash == provided_refresh_hash,
                GuestSession.revoked_at.is_(None),
                GuestSession.expires_at > now,
            )
            .values(
                refresh_token_hash=refresh_token_hash,
                expires_at=expires_at,
                updated_at=now,
            )
            .returning(GuestSession.id)
        )
        if guest_id is None:
            terminal_session_id = db.scalar(
                select(GuestSession.id).where(
                    GuestSession.refresh_token_hash == provided_refresh_hash,
                    or_(
                        GuestSession.revoked_at.is_not(None),
                        GuestSession.expires_at <= now,
                    ),
                )
            )
            return _invalid_guest_refresh_response(clear_cookie=terminal_session_id is not None)
    elif token:
        return _invalid_guest_refresh_response()
    else:
        guest_id = str(uuid4())
        db.add(
            GuestSession(
                id=guest_id,
                refresh_token_hash=refresh_token_hash,
                expires_at=expires_at,
            )
        )

    db.commit()
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        subject=guest_id,
        expires_delta=access_token_expires,
        extra_claims={"sub_type": "guest", "sid": guest_id},
    )
    _set_guest_refresh_cookie(response, refresh_token)
    return Token(access_token=access_token, token_type="bearer", guest_id=guest_id)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="清除登录会话",
)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_auth_cookie(response)
    return response


@router.get(
    "/me",
    response_model=UserResponse,
    summary="获取当前用户信息",
    responses={401: {"model": ErrorResponse}},
)
async def read_current_user(current_user: CurrentUserDep) -> UserResponse:
    return current_user


@router.patch(
    "/me/profile",
    response_model=UserResponse,
    summary="更新当前用户个人资料",
    responses={401: {"model": ErrorResponse}},
)
async def update_current_user_profile(
    payload: UserProfileUpdate,
    current_user: CurrentUserDep,
    db: SessionDep,
) -> UserResponse:
    if payload.firstName is not None:
        current_user.first_name = payload.firstName
    if payload.surname is not None:
        current_user.surname = payload.surname
    if payload.role is not None:
        current_user.job_role = payload.role
    if payload.organization is not None:
        current_user.organization = payload.organization
    if payload.industry is not None:
        current_user.industry = payload.industry

    db.commit()
    db.refresh(current_user)
    return current_user
