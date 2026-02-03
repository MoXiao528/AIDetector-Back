from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.deps import CurrentUserDep, SessionDep
from app.models.user import User
from app.schemas import ErrorResponse, LoginRequest, RegisterRequest, Token, UserProfileUpdate, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post(
    "/register",
    response_model=UserResponse,
    summary="用户注册",
    status_code=status.HTTP_201_CREATED,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def register_user(payload: RegisterRequest, db: SessionDep) -> UserResponse:
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
async def login(payload: LoginRequest, db: SessionDep) -> Token:
    if "@" in payload.identifier:
        user = db.scalar(select(User).where(User.email == payload.identifier))
    else:
        user = db.scalar(select(User).where(User.name == payload.identifier))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_USER_NOT_FOUND",
                "message": "User not found",
                "detail": "User not found",
            },
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_INVALID_PASSWORD",
                "message": "Invalid password",
                "detail": "Invalid password",
            },
        )

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(subject=str(user.id), expires_delta=access_token_expires)
    return Token(access_token=access_token, token_type="bearer")


@router.post(
    "/guest",
    response_model=Token,
    summary="游客登录获取 JWT",
)
async def guest_login() -> Token:
    guest_id = str(uuid4())
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        subject=guest_id,
        expires_delta=access_token_expires,
        extra_claims={"sub_type": "guest", "guest_id": guest_id},
    )
    return Token(access_token=access_token, token_type="bearer")


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
