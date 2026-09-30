"""The sole Oracle connection path for Agora application code."""

from contextlib import contextmanager
import os
from threading import Lock
from typing import Any, Iterator, Mapping

from sqlalchemy import create_pool_from_url
from sqlalchemy.pool import Pool
from treasury_analytics import TAConnection

from agora.core.config import environment


class StorageUnavailable(RuntimeError):
    """Agora could not establish an Oracle connection."""


_pools: dict[str, Pool] = {}
_pool_lock = Lock()


def _integer_setting(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _boolean_setting(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be 1 or 0")


def _pool(selected: str) -> Pool:
    with _pool_lock:
        pool = _pools.get(selected)
        if pool is None:
            pool = create_pool_from_url(
                "oracle+oracledb://",
                creator=TAConnection(env=selected).connect,
                pool_size=_integer_setting("AGORA_DB_POOL_SIZE", 5, 1),
                max_overflow=_integer_setting("AGORA_DB_POOL_MAX_OVERFLOW", 5, 0),
                pool_timeout=_integer_setting("AGORA_DB_POOL_TIMEOUT", 30, 1),
                pool_recycle=_integer_setting("AGORA_DB_POOL_RECYCLE", 1800, 1),
                pool_pre_ping=_boolean_setting("AGORA_DB_POOL_PRE_PING", True),
                pool_use_lifo=_boolean_setting("AGORA_DB_POOL_USE_LIFO", True),
            )
            _pools[selected] = pool
        return pool


def _connect_oracle() -> Any:
    return _pool(environment()).connect()


@contextmanager
def connection() -> Iterator[Any]:
    try:
        conn = _connect_oracle()
    except Exception as exc:
        raise StorageUnavailable("Oracle connection unavailable") from exc
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[Any]:
    with connection() as conn:
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _rows(cursor: Any) -> list[dict[str, Any]]:
    columns = [item[0].lower() for item in cursor.description or ()]
    result: list[dict[str, Any]] = []
    for values in cursor:
        row = dict(zip(columns, values))
        for key, value in row.items():
            if hasattr(value, "read") and callable(value.read):
                row[key] = value.read()
        result.append(row)
    return result


def query_all(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, dict(params or {}))
        return _rows(cursor)


def query_one(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(sql, dict(params or {}))
        columns = [item[0].lower() for item in cursor.description or ()]
        values = cursor.fetchone()
        if values is None:
            return None
        row = dict(zip(columns, values))
        for key, value in row.items():
            if hasattr(value, "read") and callable(value.read):
                row[key] = value.read()
        return row


def execute(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> int:
    with conn.cursor() as cursor:
        cursor.execute(sql, dict(params or {}))
        return cursor.rowcount
