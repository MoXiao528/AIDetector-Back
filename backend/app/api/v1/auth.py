from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.deps import CurrentUserDep, SessionDep
from app.models.user import User
from app.schemas import ErrorResponse, LoginRequest, RegisterRequest, Token, UserResponse

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
    if payload.username and "@" in payload.username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "AUTH_USERNAME_INVALID",
                "message": "Invalid username",
                "detail": "Username must not contain '@'",
            },
        )
    existing_user = db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")
    if payload.username:
        existing_username = db.scalar(select(User).where(User.username == payload.username))
        if existing_username:
            raise HTTPException(status_code=409, detail="Username already registered")

    display_name = payload.name or payload.email.split("@", maxsplit=1)[0]

    user = User(
        email=payload.email,
        username=payload.username,
        name=display_name,
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
    identifier = payload.identifier or payload.email
    if identifier is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_USER_NOT_FOUND",
                "message": "User not found",
                "detail": "User not found",
            },
        )
    if "@" in identifier:
        user = db.scalar(select(User).where(User.email == identifier))
    else:
        user = db.scalar(select(User).where(User.username == identifier))
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


@router.get(
    "/me",
    response_model=UserResponse,
    summary="获取当前用户信息",
    responses={401: {"model": ErrorResponse}},
)
async def read_current_user(current_user: CurrentUserDep) -> UserResponse:
    return current_user
