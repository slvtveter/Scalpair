"""JWT auth scaffolding (register / login / me) with subscription tiers.

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library — no
native dependencies, no crypto footguns beyond iteration count tuning.
The platform is strictly analytical: we never handle exchange API keys,
wallet keys, or anything that could sign orders.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.ratelimit import auth_rate_limiter
from app.config import get_settings
from app.db import User, get_session_factory

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

bearer_scheme = HTTPBearer(auto_error=False)

# brute-force protection: 10 attempts / minute / IP on credential endpoints
_auth_rl = auth_rate_limiter(limit=10, window_s=60.0)

PBKDF2_ITERATIONS = 240_000


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_token(user_id: int, tier: str) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id),
        "tier": tier,
        "iat": int(time.time()),
        "exp": int(time.time()) + s.jwt_expire_minutes * 60,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User | None:
    """Resolve the caller; returns None for anonymous users (public data is free)."""
    if creds is None:
        return None
    payload = decode_token(creds.credentials)
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        user = await sess.get(User, int(payload.get("sub", 0)))
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
        return user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    subscription_tier: str
    created_at: float


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    subscription_tier: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/register", response_model=UserOut, status_code=201, dependencies=[Depends(_auth_rl)])
async def register(body: RegisterIn) -> UserOut:
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        existing = await sess.scalar(select(User).where(User.email == body.email.lower()))
        if existing:
            raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
        user = User(email=body.email.lower(), password_hash=hash_password(body.password))
        sess.add(user)
        await sess.commit()
        await sess.refresh(user)
        return UserOut(
            id=user.id, email=user.email, subscription_tier=user.subscription_tier, created_at=user.created_at
        )


@router.post("/login", response_model=TokenOut, dependencies=[Depends(_auth_rl)])
async def login(body: LoginIn) -> TokenOut:
    factory = get_session_factory(get_settings().database_url)
    async with factory() as sess:
        user = await sess.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenOut(access_token=create_token(user.id, user.subscription_tier), subscription_tier=user.subscription_tier)


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User | None, Depends(current_user)]) -> UserOut:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return UserOut(id=user.id, email=user.email, subscription_tier=user.subscription_tier, created_at=user.created_at)
