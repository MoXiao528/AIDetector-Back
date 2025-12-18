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
    existing_user = db.scalar(select(User).where(User.email == payload.email))
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=payload.email, password_hash=get_password_hash(payload.password))
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
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

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
