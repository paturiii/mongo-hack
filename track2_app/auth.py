"""Plain MongoDB auth (APP-03): scrypt password hashes in `users`, opaque tokens in `auth_sessions`."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException
from pymongo.errors import DuplicateKeyError

from shared.db import db

COOKIE = "wander_session"
SESSION_TTL = timedelta(days=30)
_N, _R, _P = 2**14, 8, 1


def now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P)
    return hmac.compare_digest(calc.hex(), h)


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signup(email: str, password: str, name: str) -> dict:
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    user = {"_id": f"usr_{secrets.token_hex(8)}", "email": email.strip().lower(), "name": name.strip() or email.split("@")[0],
            "plan": "free", "password_hash": hash_password(password), "api_key_hashes": [],
            "connected_clients": ["native"], "notify": {"email": True, "voice": True}, "created_at": now()}
    try:
        db().users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(409, "An account with that email already exists.") from None
    return user


def login(email: str, password: str) -> tuple[dict, str]:
    user = db().users.find_one({"email": email.strip().lower()})
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password.")
    token = secrets.token_urlsafe(32)
    db().auth_sessions.insert_one({"_id": _token_id(token), "user_id": user["_id"], "created_at": now(),
                                   "expires_at": now() + SESSION_TTL})
    return user, token


def logout(token: str | None) -> None:
    if token:
        db().auth_sessions.delete_one({"_id": _token_id(token)})


def user_from_token(token: str | None) -> dict | None:
    if not token:
        return None
    s = db().auth_sessions.find_one({"_id": _token_id(token), "expires_at": {"$gt": now()}})
    return db().users.find_one({"_id": s["user_id"]}, {"password_hash": 0}) if s else None


def current_user(wander_session: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency: 401 unless a valid, unexpired session cookie is present."""
    user = user_from_token(wander_session)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


def public_user(u: dict) -> dict:
    return {"id": u["_id"], "email": u["email"], "name": u.get("name"), "plan": u.get("plan", "free")}
