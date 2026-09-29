"""Account authentication, durable sessions, and account support endpoints."""

from __future__ import annotations

import hmac
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from agora.core.db import connection, execute, query_all, query_one, transaction
from agora.core.security import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    hash_password,
    hash_session_token,
    new_csrf_token,
    new_session_token,
    verify_password,
)


SESSION_COOKIE = "agora_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}\Z")
_USERNAME_DUMMY_HASH = hash_password("Agora unknown account timing pad 2026")

router = APIRouter()


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    username: str
    full_name: str
    is_admin: bool
    csrf_token: str


class Credentials(BaseModel):
    username: str
    password: str


class Registration(Credentials):
    full_name: str


class PasswordReset(BaseModel):
    new_password: str


def _raise_api_error(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": details}},
    )


def _cookie_secure() -> bool:
    if os.getenv("AGORA_ALLOW_INSECURE_HTTP", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return os.getenv("ENV", "DEV").strip().upper() == "PROD"


def _actor(row: dict[str, Any]) -> Actor:
    return Actor(
        id=str(row["id"]),
        username=str(row["username"]),
        full_name=str(row["full_name"]),
        is_admin=int(row.get("is_admin") or 0) == 1,
        csrf_token=str(row["csrf_token"]),
    )


def _public_user(actor: Actor) -> dict[str, Any]:
    return {
        "id": actor.id,
        "username": actor.username,
        "full_name": actor.full_name,
        "is_admin": actor.is_admin,
    }


def _auth_payload(actor: Actor) -> dict[str, Any]:
    return {"user": _public_user(actor), "csrf_token": actor.csrf_token}


def _utc_iso(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _normalize_username(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        _raise_api_error(422, "invalid_username", "Enter a valid username.")
    username = unicodedata.normalize("NFKC", value).strip()
    if not _USERNAME_PATTERN.fullmatch(username):
        _raise_api_error(
            422,
            "invalid_username",
            "Username must be 3–64 letters, numbers, dots, underscores, or hyphens, and start with a letter or number.",
        )
    return username, username.casefold()


def _validated_full_name(value: str) -> str:
    if not isinstance(value, str):
        _raise_api_error(422, "invalid_full_name", "Enter your full name.")
    full_name = " ".join(value.split())
    try:
        encoded_length = len(full_name.encode("utf-8"))
    except UnicodeEncodeError:
        _raise_api_error(422, "invalid_full_name", "Enter a valid full name.")
    if not full_name or encoded_length > 200:
        _raise_api_error(422, "invalid_full_name", "Full name must be between 1 and 200 bytes.")
    return full_name


def _validate_password(value: str) -> str:
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        _raise_api_error(
            422,
            "invalid_password",
            f"Password must be between {PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters.",
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _raise_api_error(422, "invalid_password", "Enter a valid password.")
    return value


def _origin_key(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid origin")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Invalid origin") from None
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    return scheme, host, port


def _validate_origin_if_supplied(request: Request) -> None:
    supplied = request.headers.get("origin")
    if not supplied:
        return
    configured_origin = os.getenv("AGORA_PUBLIC_ORIGIN", "").strip()
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    expected = configured_origin or request_origin
    try:
        is_same_origin = _origin_key(supplied) == _origin_key(expected)
    except ValueError:
        is_same_origin = False
    if not is_same_origin:
        _raise_api_error(403, "origin_mismatch", "This request did not come from the application origin.")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _insert_session(conn: Any, account_id: str) -> tuple[str, str]:
    token = new_session_token()
    csrf_token = new_csrf_token()
    execute(
        conn,
        """INSERT INTO TB_TA_AGORA_SESSIONS
               (token_hash, account_id, csrf_token, created_at, expires_at)
             VALUES
               (:token_hash, :account_id, :csrf_token, SYSTIMESTAMP,
                SYSTIMESTAMP + NUMTODSINTERVAL(:ttl_seconds, 'SECOND'))""",
        {
            "token_hash": hash_session_token(token),
            "account_id": account_id,
            "csrf_token": csrf_token,
            "ttl_seconds": SESSION_TTL_SECONDS,
        },
    )
    return token, csrf_token


def _query_account_by_username(username_norm: str) -> dict[str, Any] | None:
    with connection() as conn:
        return query_one(
            conn,
            """SELECT id, username, username_norm, full_name, password_hash, is_admin
                 FROM TB_TA_AGORA_USERS
                WHERE username_norm = :username_norm""",
            {"username_norm": username_norm},
        )


def _query_login_account(username_norm: str) -> dict[str, Any] | None:
    with connection() as conn:
        return query_one(
            conn,
            """SELECT a.id, a.username, a.username_norm, a.full_name, a.password_hash,
                      a.is_admin,
                      CASE WHEN l.locked_until > SYSTIMESTAMP THEN 1 ELSE 0 END AS is_locked
                 FROM TB_TA_AGORA_USERS a
                 LEFT JOIN TB_TA_AGORA_LOGIN_ATTEMPTS l ON l.account_id = a.id
                WHERE a.username_norm = :username_norm""",
            {"username_norm": username_norm},
        )


def _record_login_failure(account_id: str) -> None:
    with transaction() as conn:
        execute(
            conn,
            """MERGE INTO TB_TA_AGORA_LOGIN_ATTEMPTS target
             USING (SELECT :account_id AS account_id FROM dual) incoming
                ON (target.account_id = incoming.account_id)
              WHEN MATCHED THEN UPDATE SET
                   failed_attempts = CASE
                       WHEN target.window_started_at <= SYSTIMESTAMP - NUMTODSINTERVAL(:window_seconds, 'SECOND')
                       THEN 1 ELSE LEAST(target.failed_attempts + 1, :max_attempts) END,
                   window_started_at = CASE
                       WHEN target.window_started_at <= SYSTIMESTAMP - NUMTODSINTERVAL(:window_seconds, 'SECOND')
                       THEN SYSTIMESTAMP ELSE target.window_started_at END,
                   locked_until = CASE
                       WHEN CASE
                           WHEN target.window_started_at <= SYSTIMESTAMP - NUMTODSINTERVAL(:window_seconds, 'SECOND')
                           THEN 1 ELSE target.failed_attempts + 1 END >= :max_attempts
                       THEN SYSTIMESTAMP + NUMTODSINTERVAL(:lock_seconds, 'SECOND')
                       ELSE NULL END,
                   updated_at = SYSTIMESTAMP
              WHEN NOT MATCHED THEN INSERT
                   (account_id, failed_attempts, window_started_at, locked_until, updated_at)
                   VALUES (incoming.account_id, 1, SYSTIMESTAMP, NULL, SYSTIMESTAMP)""",
            {
                "account_id": account_id,
                "window_seconds": 15 * 60,
                "max_attempts": 5,
                "lock_seconds": 15 * 60,
            },
        )


def _unavailable() -> None:
    _raise_api_error(503, "storage_unavailable", "Agora could not reach account storage. Try again shortly.")


def require_actor(request: Request) -> Actor:
    """Resolve the current account from an unexpired, hash-only Oracle session."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        _raise_api_error(401, "authentication_required", "Sign in to continue.")
    try:
        with connection() as conn:
            row = query_one(
                conn,
                """SELECT a.id, a.username, a.full_name, a.is_admin, s.csrf_token
                     FROM TB_TA_AGORA_SESSIONS s
                     JOIN TB_TA_AGORA_USERS a ON a.id = s.account_id
                    WHERE s.token_hash = :token_hash
                      AND s.expires_at > SYSTIMESTAMP""",
                {"token_hash": hash_session_token(token)},
            )
    except Exception:
        _unavailable()
    if row is None:
        _raise_api_error(401, "authentication_required", "Sign in to continue.")
    return _actor(row)


def require_csrf(request: Request, actor: Actor = Depends(require_actor)) -> Actor:
    """Require the session's memory-held CSRF token on state-changing requests."""
    _validate_origin_if_supplied(request)
    supplied = request.headers.get("x-csrf-token")
    if not supplied or not _constant_time_equal(supplied, actor.csrf_token):
        _raise_api_error(403, "csrf_failed", "Refresh your session and try again.")
    return actor


def _constant_time_equal(left: str, right: str) -> bool:
    # The CSRF token alphabet is ASCII; reject other input before constant-time comparison.
    try:
        left.encode("ascii")
        right.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(left, right)


def require_admin(actor: Actor = Depends(require_actor)) -> Actor:
    if not actor.is_admin:
        _raise_api_error(403, "administrator_required", "Administrator access is required.")
    return actor


@router.post("/api/auth/register", status_code=201)
def register(body: Registration, request: Request, response: Response) -> dict[str, Any]:
    _validate_origin_if_supplied(request)
    username, username_norm = _normalize_username(body.username)
    full_name = _validated_full_name(body.full_name)
    password = _validate_password(body.password)
    password_hash = hash_password(password)
    account_id = str(uuid.uuid4())
    try:
        with transaction() as conn:
            existing = query_one(
                conn,
                "SELECT id FROM TB_TA_AGORA_USERS WHERE username_norm = :username_norm",
                {"username_norm": username_norm},
            )
            if existing is not None:
                _raise_api_error(409, "username_taken", "That username is already in use.")
            execute(
                conn,
                """INSERT INTO TB_TA_AGORA_USERS
                       (id, username, username_norm, full_name, password_hash, is_admin,
                        created_at, updated_at)
                     VALUES
                       (:id, :username, :username_norm, :full_name, :password_hash, 0,
                        SYSTIMESTAMP, SYSTIMESTAMP)""",
                {
                    "id": account_id,
                    "username": username,
                    "username_norm": username_norm,
                    "full_name": full_name,
                    "password_hash": password_hash,
                },
            )
            token, csrf_token = _insert_session(conn, account_id)
    except HTTPException:
        raise
    except Exception:
        try:
            existing = _query_account_by_username(username_norm)
        except Exception:
            _unavailable()
        if existing is not None:
            _raise_api_error(409, "username_taken", "That username is already in use.")
        _unavailable()
    actor = Actor(account_id, username, full_name, False, csrf_token)
    _set_session_cookie(response, token)
    return _auth_payload(actor)


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    _validate_origin_if_supplied(request)
    try:
        _, username_norm = _normalize_username(body.username)
    except HTTPException:
        username_norm = ""
    password = body.password if isinstance(body.password, str) else ""
    account: dict[str, Any] | None = None
    try:
        if username_norm:
            account = _query_login_account(username_norm)
    except Exception:
        _unavailable()
    is_locked = account is not None and int(account.get("is_locked") or 0) == 1
    stored_hash = (
        _USERNAME_DUMMY_HASH
        if account is None or is_locked
        else str(account["password_hash"])
    )
    password_matches = verify_password(password, stored_hash)
    if account is None or is_locked or not password_matches:
        if account is not None and not is_locked and not password_matches:
            try:
                _record_login_failure(str(account["id"]))
            except Exception:
                _unavailable()
        _raise_api_error(401, "invalid_credentials", "Username or password is incorrect.")
    try:
        with transaction() as conn:
            token, csrf_token = _insert_session(conn, str(account["id"]))
            execute(
                conn,
                "DELETE FROM TB_TA_AGORA_LOGIN_ATTEMPTS WHERE account_id = :account_id",
                {"account_id": str(account["id"])},
            )
    except Exception:
        _unavailable()
    actor = Actor(
        id=str(account["id"]),
        username=str(account["username"]),
        full_name=str(account["full_name"]),
        is_admin=int(account.get("is_admin") or 0) == 1,
        csrf_token=csrf_token,
    )
    _set_session_cookie(response, token)
    return _auth_payload(actor)


@router.post("/api/auth/logout")
def logout(request: Request, actor: Actor = Depends(require_csrf)) -> Response:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        with transaction() as conn:
            execute(
                conn,
                "DELETE FROM TB_TA_AGORA_SESSIONS WHERE token_hash = :token_hash AND account_id = :account_id",
                {"token_hash": hash_session_token(token), "account_id": actor.id},
            )
    except Exception:
        _unavailable()
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/api/auth/me")
def me(actor: Actor = Depends(require_actor)) -> dict[str, Any]:
    return _auth_payload(actor)


@router.get("/api/admin/accounts")
def list_accounts(actor: Actor = Depends(require_admin)) -> dict[str, list[dict[str, Any]]]:
    try:
        with transaction() as conn:
            rows = query_all(
                conn,
                """SELECT id, username, full_name, is_admin, created_at
                     FROM TB_TA_AGORA_USERS
                    ORDER BY username_norm""",
            )
            execute(
                conn,
                """INSERT INTO TB_TA_AGORA_AUDIT
                       (id, actor_id, action, project_id, subject_id, details, created_at)
                     VALUES
                       (:id, :actor_id, :action, NULL, NULL, :details, SYSTIMESTAMP)""",
                {
                    "id": str(uuid.uuid4()),
                    "actor_id": actor.id,
                    "action": "admin.accounts.list",
                    "details": json.dumps({"account_count": len(rows)}, separators=(",", ":")),
                },
            )
    except Exception:
        _unavailable()
    accounts = [
        {
            "id": str(row["id"]),
            "username": str(row["username"]),
            "full_name": str(row["full_name"]),
            "is_admin": int(row.get("is_admin") or 0) == 1,
            "created_at": _utc_iso(row.get("created_at")),
        }
        for row in rows
    ]
    return {"accounts": accounts}


@router.post("/api/admin/accounts/{username}/reset-password")
def reset_account_password(
    username: str,
    body: PasswordReset,
    actor: Actor = Depends(require_csrf),
) -> dict[str, bool]:
    if not actor.is_admin:
        _raise_api_error(403, "administrator_required", "Administrator access is required.")
    try:
        _, username_norm = _normalize_username(username)
    except HTTPException:
        _raise_api_error(404, "account_not_found", "No account has that username.")
    password = _validate_password(body.new_password)
    password_hash = hash_password(password)
    try:
        with transaction() as conn:
            target = query_one(
                conn,
                "SELECT id, username FROM TB_TA_AGORA_USERS WHERE username_norm = :username_norm",
                {"username_norm": username_norm},
            )
            if target is None:
                _raise_api_error(404, "account_not_found", "No account has that username.")
            execute(
                conn,
                """UPDATE TB_TA_AGORA_USERS
                      SET password_hash = :password_hash, updated_at = SYSTIMESTAMP
                    WHERE id = :account_id""",
                {"password_hash": password_hash, "account_id": target["id"]},
            )
            execute(
                conn,
                "DELETE FROM TB_TA_AGORA_SESSIONS WHERE account_id = :account_id",
                {"account_id": target["id"]},
            )
            execute(
                conn,
                """INSERT INTO TB_TA_AGORA_AUDIT
                       (id, actor_id, action, project_id, subject_id, details, created_at)
                     VALUES
                       (:id, :actor_id, :action, NULL, :subject_id, :details, SYSTIMESTAMP)""",
                {
                    "id": str(uuid.uuid4()),
                    "actor_id": actor.id,
                    "action": "admin.account.password_reset",
                    "subject_id": str(target["id"]),
                    "details": json.dumps({"username": str(target["username"])}, separators=(",", ":")),
                },
            )
    except HTTPException:
        raise
    except Exception:
        _unavailable()
    return {"ok": True}
