from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt
from pymongo.errors import DuplicateKeyError

from .storage import ChatStore, timestamp


ACCESS_SECONDS = 1800
REFRESH_DAYS = 30
COOKIE_NAME = "refresh_token"
SSE_COOKIE_NAME = "sse_access_token"
HASHER = PasswordHasher()


class AuthError(Exception):
    pass


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=ACCESS_SECONDS)}, _secret(), algorithm="HS256")


def decode_access(token: str) -> int:
    try:
        return int(jwt.decode(token, _secret(), algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise AuthError("invalid access token") from exc


def register(store: ChatStore, email: str, password: str) -> dict:
    user = {
        "_id": store.next_id("user"),
        "email": email.strip().lower(),
        "password_hash": HASHER.hash(password),
        "created_at": timestamp(),
    }
    try:
        store.users.insert_one(user)
    except DuplicateKeyError as exc:
        raise AuthError("email already registered") from exc
    return user


def verify_login(store: ChatStore, email: str, password: str) -> dict:
    user = store.users.find_one({"email": email.strip().lower()})
    if user is None:
        raise AuthError("invalid credentials")
    try:
        HASHER.verify(user["password_hash"], password)
    except VerifyMismatchError as exc:
        raise AuthError("invalid credentials") from exc
    return user


def create_refresh(store: ChatStore, user_id: int) -> str:
    token = secrets.token_urlsafe(48)
    store.sessions.insert_one({
        "_id": sha256(token.encode()).hexdigest(),
        "user_id": user_id,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS),
    })
    return token


def rotate_refresh(store: ChatStore, token: str) -> tuple[int, str]:
    session = store.sessions.find_one_and_delete({"_id": sha256(token.encode()).hexdigest()})
    if not session or session["expires_at"].replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise AuthError("invalid refresh token")
    return session["user_id"], create_refresh(store, session["user_id"])


def revoke_refresh(store: ChatStore, token: str) -> None:
    store.sessions.delete_one({"_id": sha256(token.encode()).hexdigest()})


def user_view(user: dict) -> dict:
    return {"id": user["_id"], "email": user["email"], "created_at": user["created_at"]}
