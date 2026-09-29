"""Project-granted, backend-only Starburst source configuration and bounded reads."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
import math
from typing import Any

from agora.core.config import environment
from agora.core.db import query_all, query_one, transaction
from agora.core.projects import audit
from agora.data.starburst import MAX_QUERY_ROWS, StarburstConfig, catalogs, connection as starburst_connection
from agora.data.starburst import read_rows as read_starburst_rows, schemas, tables


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SOURCE_KEY = re.compile(r"[a-z][a-z0-9_-]{0,79}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
MAX_ROWS = MAX_QUERY_ROWS
MAX_RESULT_BYTES = 2 * 1024 * 1024


class SourceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _environment() -> str:
    return environment()


def validate_source_config(config: dict[str, Any]) -> dict[str, Any]:
    key = str(config.get("source_key", "")).strip()
    name = str(config.get("display_name", "")).strip()
    host = str(config.get("host", "")).strip()
    environment = str(config.get("environment", _environment())).strip().upper()
    user_env = str(config.get("user_env", "")).strip()
    password_env = str(config.get("password_env", "")).strip()
    approved_catalogs = config.get("approved_catalogs")
    raw_port = config.get("port", 443)
    try:
        if isinstance(raw_port, (bool, float)):
            raise ValueError
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise SourceError("invalid_port", "Port must be an integer from 1 to 65535.") from exc
    if not SOURCE_KEY.fullmatch(key):
        raise SourceError("invalid_source_key", "Source key needs lowercase letters, digits, _ or -.")
    if not name or len(name.encode("utf-8")) > 160:
        raise SourceError("invalid_display_name", "Source name must be 1 to 160 UTF-8 bytes.")
    if not host or len(host) > 255 or any(char in host for char in "/:@?# "):
        raise SourceError("invalid_host", "Host must be a hostname without URL components.")
    if environment not in ("DEV", "PROD"):
        raise SourceError("invalid_environment", "Environment must be DEV or PROD.")
    if not 1 <= port <= 65535:
        raise SourceError("invalid_port", "Port must be an integer from 1 to 65535.")
    if not ENV_NAME.fullmatch(user_env) or not ENV_NAME.fullmatch(password_env):
        raise SourceError("invalid_credential_reference", "Credential references must be environment variable names.")
    if not isinstance(approved_catalogs, list) or not 1 <= len(approved_catalogs) <= 32:
        raise SourceError("invalid_catalogs", "Choose 1 to 32 approved catalogs.")
    if any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) for item in approved_catalogs):
        raise SourceError("invalid_catalogs", "Catalog names must be simple SQL identifiers.")
    if len(set(approved_catalogs)) != len(approved_catalogs):
        raise SourceError("invalid_catalogs", "Catalog names must be unique.")
    return {
        "source_key": key,
        "display_name": name,
        "environment": environment,
        "host": host,
        "port": port,
        "http_scheme": "https",
        "user_env": user_env,
        "password_env": password_env,
        "approved_catalogs_json": json.dumps(approved_catalogs),
    }


def create_source(config: dict[str, Any], actor_id: str) -> dict[str, Any]:
    validated = validate_source_config(config)
    source_id = str(uuid.uuid4())
    with transaction() as conn:
        existing = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_DATA_SOURCES WHERE source_key = :source_key",
            {"source_key": validated["source_key"]},
        )
        if existing is not None:
            raise SourceError("source_exists", "A source with this key already exists.", 409)
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO TB_TA_AGORA_DATA_SOURCES
                   (id, source_key, display_name, environment, host, port, http_scheme,
                    user_env, password_env, approved_catalogs_json, enabled, created_by)
                   VALUES
                   (:id, :source_key, :display_name, :environment, :host, :port, :http_scheme,
                    :user_env, :password_env, :approved_catalogs_json, 1, :created_by)""",
                {"id": source_id, "created_by": actor_id, **validated},
            )
        audit(conn, actor_id, "source.created", subject_id=source_id)
    return {"id": source_id, **_public_source(validated), "enabled": True}


def grant_source(project_id: str, source_id: str, actor_id: str) -> None:
    with transaction() as conn:
        source = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_DATA_SOURCES WHERE id = :id AND enabled = 1 AND environment = :environment",
            {"id": source_id, "environment": _environment()},
        )
        if source is None:
            raise SourceError("source_not_found", "Source is unavailable.", 404)
        with conn.cursor() as cursor:
            cursor.execute(
                """MERGE INTO TB_TA_AGORA_PROJECT_SOURCES p
                   USING (SELECT :project_id project_id, :source_id source_id FROM dual) s
                   ON (p.project_id = s.project_id AND p.source_id = s.source_id)
                   WHEN NOT MATCHED THEN INSERT
                     (project_id, source_id, granted_by) VALUES
                     (s.project_id, s.source_id, :granted_by)""",
                {"project_id": project_id, "source_id": source_id, "granted_by": actor_id},
            )
        audit(conn, actor_id, "source.granted", project_id, source_id)


def revoke_source(project_id: str, source_id: str, actor_id: str) -> None:
    with transaction() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM TB_TA_AGORA_PROJECT_SOURCES WHERE project_id = :project_id AND source_id = :source_id",
                {"project_id": project_id, "source_id": source_id},
            )
        audit(conn, actor_id, "source.revoked", project_id, source_id)


def list_project_sources(project_id: str) -> list[dict[str, Any]]:
    with transaction() as conn:
        rows = query_all(
            conn,
            """SELECT s.id, s.source_key, s.display_name, s.environment, s.approved_catalogs_json
               FROM TB_TA_AGORA_DATA_SOURCES s
               JOIN TB_TA_AGORA_PROJECT_SOURCES p ON p.source_id = s.id
               WHERE p.project_id = :project_id AND s.enabled = 1 AND s.environment = :environment
               ORDER BY s.display_name""",
            {"project_id": project_id, "environment": _environment()},
        )
    return [_public_source(row) for row in rows]


def list_available_sources() -> list[dict[str, Any]]:
    with transaction() as conn:
        rows = query_all(
            conn,
            """SELECT id, source_key, display_name, environment, approved_catalogs_json, enabled
               FROM TB_TA_AGORA_DATA_SOURCES WHERE environment = :environment ORDER BY display_name""",
            {"environment": _environment()},
        )
    return [{**_public_source(row), "enabled": bool(row["enabled"])} for row in rows]


def _public_source(row: dict[str, Any]) -> dict[str, Any]:
    names = ("id", "source_key", "display_name", "environment")
    result = {name: row[name] for name in names if name in row}
    catalogs = row.get("approved_catalogs_json")
    if catalogs is not None:
        result["approved_catalogs"] = json.loads(catalogs)
    return result


def _project_source(project_id: str, source_id: str) -> dict[str, Any]:
    with transaction() as conn:
        row = query_one(
            conn,
            """SELECT s.id, s.display_name, s.host, s.port, s.http_scheme,
                      s.user_env, s.password_env, s.approved_catalogs_json
               FROM TB_TA_AGORA_DATA_SOURCES s
               JOIN TB_TA_AGORA_PROJECT_SOURCES p ON p.source_id = s.id
               WHERE p.project_id = :project_id AND s.id = :source_id
                 AND s.enabled = 1 AND s.environment = :environment""",
            {"project_id": project_id, "source_id": source_id, "environment": _environment()},
        )
    if row is None:
        raise SourceError("source_not_found", "Source is unavailable to this project.", 404)
    return row


def _project_source_connection(project_id: str, source_id: str) -> tuple[StarburstConfig, list[str]]:
    row = _project_source(project_id, source_id)
    user = os.getenv(row["user_env"])
    password = os.getenv(row["password_env"])
    if not user or not password:
        raise SourceError("source_unconfigured", "Source credentials are not configured.", 503)
    config = StarburstConfig(
        host=row["host"],
        port=int(row["port"]),
        http_scheme=row["http_scheme"],
        user=user,
        password=password,
    )
    return config, json.loads(row["approved_catalogs_json"])


def browse_source(
    project_id: str, source_id: str, level: str, catalog: str | None = None, schema: str | None = None
) -> list[str]:
    config, approved_catalogs = _project_source_connection(project_id, source_id)
    with starburst_connection(config) as conn:
        if level == "catalogs":
            return catalogs(conn, approved_catalogs)
        if not catalog or not IDENTIFIER.fullmatch(catalog):
            raise SourceError("invalid_catalog", "Choose a valid catalog.")
        if level == "schemas":
            return schemas(conn, catalog, approved_catalogs)
        if not schema or not IDENTIFIER.fullmatch(schema):
            raise SourceError("invalid_schema", "Choose a valid schema.")
        return tables(conn, catalog, schema, approved_catalogs)


def read_source_rows(project_id: str, source_id: str, request: dict[str, Any]) -> dict[str, Any]:
    catalog = request.get("catalog")
    schema = request.get("schema")
    table = request.get("table")
    columns = request.get("columns")
    raw_limit = request.get("limit", 100)
    try:
        if isinstance(raw_limit, (bool, float)):
            raise ValueError
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise SourceError("invalid_limit", "Limit must be 1 to 1000.") from exc
    if not 1 <= limit <= MAX_ROWS:
        raise SourceError("invalid_limit", "Limit must be 1 to 1000.")
    if any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in (catalog, schema, table)):
        raise SourceError("invalid_table", "Choose a valid catalog, schema, and table.")
    if columns is not None and (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 100
        or any(not isinstance(name, str) or not IDENTIFIER.fullmatch(name) for name in columns)
    ):
        raise SourceError("invalid_columns", "Choose up to 100 simple column names.")
    if columns is not None and len({name.casefold() for name in columns}) != len(columns):
        raise SourceError("invalid_columns", "Column names cannot be repeated.")
    config, approved_catalogs = _project_source_connection(project_id, source_id)
    with starburst_connection(config) as conn:
        available_catalogs = catalogs(conn, approved_catalogs)
        if catalog not in available_catalogs or schema not in schemas(conn, catalog, approved_catalogs):
            raise SourceError("table_not_found", "Table is unavailable.", 404)
        if table not in tables(conn, catalog, schema, approved_catalogs):
            raise SourceError("table_not_found", "Table is unavailable.", 404)
        result_columns, rows = read_starburst_rows(
            conn,
            catalog=catalog,
            schema=schema,
            table=table,
            columns=columns,
            limit=limit,
        )
        response = {"columns": result_columns, "rows": [_json_safe(row) for row in rows]}
        try:
            size = len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise SourceError("source_result_invalid", "Source returned an unsupported value.", 502) from exc
        if size > MAX_RESULT_BYTES:
            raise SourceError("source_result_too_large", "Source result exceeds the 2 MiB limit.", 413)
        return response


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.hex()
    return value
