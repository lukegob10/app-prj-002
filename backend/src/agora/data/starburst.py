"""Agora's bounded, read-only connection to approved Starburst sources."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


MAX_DISCOVERY_ROWS = 1_000
MAX_QUERY_ROWS = 1_000
MAX_RESULT_BYTES = 2 * 1024 * 1024


class StarburstError(RuntimeError):
    """A configured Starburst source could not be queried safely."""


@dataclass(frozen=True)
class StarburstConfig:
    host: str
    user: str
    password: str = field(repr=False)
    port: int = 443
    http_scheme: str = "https"
    verify: bool | str = True

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip() or "://" in self.host:
            raise ValueError("Starburst host must be a hostname without a URL scheme")
        if not isinstance(self.user, str) or not self.user.strip():
            raise ValueError("Starburst user is required")
        if not isinstance(self.password, str) or not self.password:
            raise ValueError("Starburst password is required")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("Starburst port must be an integer from 1 to 65535")
        if not isinstance(self.http_scheme, str) or self.http_scheme.strip().casefold() != "https":
            raise ValueError("Starburst basic authentication requires HTTPS")
        if not isinstance(self.verify, (bool, str)) or (isinstance(self.verify, str) and not self.verify.strip()):
            raise ValueError("verify must be True, False, or a CA bundle path")
        object.__setattr__(self, "host", self.host.strip())
        object.__setattr__(self, "user", self.user.strip())
        object.__setattr__(self, "http_scheme", self.http_scheme.strip().casefold())


@contextmanager
def connection(config: StarburstConfig) -> Iterator[Any]:
    try:
        from trino.auth import BasicAuthentication
        from trino.dbapi import connect
    except ImportError as exc:
        raise StarburstError("The Trino driver is not installed") from exc

    try:
        conn = connect(
            host=config.host,
            port=config.port,
            user=config.user,
            auth=BasicAuthentication(config.user, config.password),
            http_scheme=config.http_scheme,
            verify=config.verify,
        )
    except Exception as exc:
        raise StarburstError("Could not open the configured Starburst source") from exc

    try:
        yield conn
    except BaseException:
        try:
            conn.close()
        except Exception:
            pass
        raise
    else:
        try:
            conn.close()
        except Exception as exc:
            raise StarburstError("Could not close the configured Starburst source") from exc


def catalogs(conn: Any, approved_catalogs: Iterable[str]) -> list[str]:
    approved = {name.strip().casefold() for name in approved_catalogs if name.strip()}
    if not approved:
        raise ValueError("At least one approved Starburst catalog is required")
    _, rows = _query(conn, "SHOW CATALOGS", operation="catalog discovery", max_rows=MAX_DISCOVERY_ROWS)
    return [name for (name,) in rows if str(name).casefold() in approved]


def schemas(conn: Any, catalog: str, approved_catalogs: Iterable[str]) -> list[str]:
    selected = _resolve_catalog(conn, catalog, approved_catalogs)
    _, rows = _query(
        conn,
        f"SHOW SCHEMAS FROM {_quote_identifier(selected)}",
        operation="schema discovery",
        max_rows=MAX_DISCOVERY_ROWS,
    )
    return [str(row[0]) for row in rows]


def tables(conn: Any, catalog: str, schema: str, approved_catalogs: Iterable[str]) -> list[str]:
    selected = _resolve_catalog(conn, catalog, approved_catalogs)
    _, rows = _query(
        conn,
        f"SHOW TABLES FROM {_quote_identifier(selected)}.{_quote_identifier(schema)}",
        operation="table discovery",
        max_rows=MAX_DISCOVERY_ROWS,
    )
    return [str(row[0]) for row in rows]


def read_rows(
    conn: Any,
    *,
    catalog: str,
    schema: str,
    table: str,
    columns: list[str] | None,
    limit: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_ROWS:
        raise ValueError(f"Starburst row limit must be between 1 and {MAX_QUERY_ROWS}")
    names = [catalog, schema, table, *(columns or [])]
    if any(not _is_identifier(name) for name in names):
        raise ValueError("Starburst query identifiers must be simple names")
    if columns is not None and (not columns or len(columns) > 100):
        raise ValueError("Starburst queries must select between 1 and 100 columns")
    if columns is not None and len({column.casefold() for column in columns}) != len(columns):
        raise ValueError("Starburst query columns cannot be repeated")

    projection = "*" if columns is None else ", ".join(_quote_identifier(name) for name in columns)
    statement = (
        f"SELECT {projection} FROM {_quote_identifier(catalog)}."
        f"{_quote_identifier(schema)}.{_quote_identifier(table)} LIMIT {limit}"
    )
    result_columns, rows = _query(conn, statement, operation="query", max_rows=limit)
    return result_columns, [dict(zip(result_columns, row)) for row in rows]


def _resolve_catalog(conn: Any, catalog: str, approved_catalogs: Iterable[str]) -> str:
    approved = {name.strip().casefold() for name in approved_catalogs if name.strip()}
    requested = catalog.strip().casefold()
    if requested not in approved:
        raise ValueError("Starburst catalog is not approved for this source")
    available = catalogs(conn, approved)
    selected = next((name for name in available if name.casefold() == requested), None)
    if selected is None:
        raise StarburstError("An approved Starburst catalog is unavailable")
    return selected


def _query(
    conn: Any,
    statement: str,
    *,
    operation: str,
    max_rows: int,
) -> tuple[list[str], list[tuple[Any, ...]]]:
    cursor: Any | None = None
    try:
        cursor = conn.cursor()
        cursor.execute(statement)
        columns = [str(column[0]) for column in cursor.description or ()]
        rows: list[tuple[Any, ...]] = []
        result_bytes = 0
        while len(rows) <= max_rows:
            batch = cursor.fetchmany(min(64, max_rows + 1 - len(rows)))
            if not batch:
                break
            for row in batch:
                values = tuple(row)
                rows.append(values)
                result_bytes += sum(
                    len(value) if isinstance(value, bytes)
                    else len(str(value).encode("utf-8", errors="replace"))
                    for value in values
                )
                if result_bytes > MAX_RESULT_BYTES:
                    raise StarburstError(f"Starburst {operation} exceeded the 2 MiB result limit")
            if len(rows) > max_rows:
                raise StarburstError(f"Starburst {operation} exceeded its {max_rows}-row limit")
        return columns, rows
    except StarburstError:
        raise
    except Exception as exc:
        raise StarburstError(f"Starburst {operation} failed") from exc
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def _quote_identifier(name: str) -> str:
    if not name:
        raise ValueError("Starburst identifiers cannot be empty")
    return '"' + name.replace('"', '""') + '"'


def _is_identifier(name: str) -> bool:
    return bool(name) and (name[0].isalpha() or name[0] == "_") and all(
        char.isalnum() or char == "_" for char in name
    ) and name.isascii()
