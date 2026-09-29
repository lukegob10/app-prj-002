"""The sole Oracle connection path for Agora application code."""

from contextlib import contextmanager
import os
from typing import Any, Iterator, Mapping

from agora.core.config import environment


_DEV_ORACLE_USER = "LG2254"
_DEV_ORACLE_DSN = "192.168.1.151:1521/FREEPDB1"


class StorageUnavailable(RuntimeError):
    """Agora could not establish an Oracle connection."""


def _connect_oracle() -> Any:
    selected = environment()
    use_ta_client = os.getenv("AGORA_USE_TREASURY_ANALYTICS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if use_ta_client:
        try:
            from treasury_analytics import TAConnection
        except ImportError as exc:
            raise RuntimeError("AGORA_USE_TREASURY_ANALYTICS is enabled but the package is not installed") from exc
        return TAConnection(env=selected).connect()

    user = os.getenv(f"TA_{selected}_USER")
    password = os.getenv(f"TA_{selected}_PASSWORD")
    dsn = os.getenv(f"TA_{selected}_DSN")
    if selected == "DEV":
        user = user or _DEV_ORACLE_USER
        dsn = dsn or _DEV_ORACLE_DSN
    if not all((user, password, dsn)):
        raise RuntimeError(
            "Set TA_<ENV>_PASSWORD; PROD also requires TA_PROD_USER and TA_PROD_DSN"
        )
    try:
        import oracledb
    except ImportError as exc:
        raise RuntimeError("The python-oracledb driver is not installed") from exc

    return oracledb.connect(user=user, password=password, dsn=dsn)


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
