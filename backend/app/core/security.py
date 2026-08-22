from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import hashlib
import secrets
from uuid import uuid4

import jwt
from passlib.context import CryptContext

from .config import get_settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
JWT_ISSUER = "aidetector-backend"
JWT_AUDIENCE = "aidetector-api"
REGISTERED_ACCESS_TOKEN_CLAIMS = frozenset({"sub", "iss", "aud", "iat", "exp", "jti"})


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    extra_claims: Dict[str, Any] | None = None,
) -> str:
    custom_claims = dict(extra_claims or {})
    overridden_claims = REGISTERED_ACCESS_TOKEN_CLAIMS.intersection(custom_claims)
    if overridden_claims:
        names = ", ".join(sorted(overridden_claims))
        raise ValueError(f"Registered access token claims cannot be overridden: {names}")

    now = datetime.now(timezone.utc)
    expire = now + (expires_delta if expires_delta is not None else timedelta(minutes=30))
    to_encode: Dict[str, Any] = {
        **custom_claims,
        "sub": subject,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": expire,
        "jti": str(uuid4()),
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm="HS256")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    plain_key = secrets.token_urlsafe(40)
    return plain_key, hash_api_key(plain_key)
