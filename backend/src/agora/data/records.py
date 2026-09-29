"""Governed, project-scoped JSON records stored in Oracle.

This module is a service layer for the data router. It deliberately owns no
HTTP routes so the router can choose its response and error-envelope details.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from agora.core.auth import Actor
from agora.core.db import connection, execute, query_all, query_one, transaction
from agora.core.projects import audit, get_project, require_project_role


MAX_RECORDS_PER_PROJECT = 500
MAX_DATA_BYTES = 3_500
MAX_JSON_DEPTH = 6
MAX_JSON_NODES = 256
MAX_OBJECT_KEYS = 64
MAX_ARRAY_ITEMS = 128
MAX_STRING_CHARS = 512
MAX_KEY_CHARS = 64

ExportFormat = Literal["json", "csv"]


class RecordServiceError(Exception):
    """Expected record-service failure that a router can map to the API envelope."""

    status_code = 400
    code = "record_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class RecordValidationError(RecordServiceError):
    status_code = 422
    code = "invalid_record"


class RecordForbiddenError(RecordServiceError):
    status_code = 403
    code = "record_write_disabled"


class RecordNotFoundError(RecordServiceError):
    status_code = 404
    code = "record_not_found"


class RecordConflictError(RecordServiceError):
    status_code = 409
    code = "record_revision_conflict"


class RecordLimitError(RecordServiceError):
    status_code = 413
    code = "record_limit_exceeded"


class RecordStorageError(RecordServiceError):
    status_code = 500
    code = "record_storage_error"


@dataclass(frozen=True)
class RecordExport:
    """Download body and metadata returned by :func:`export_records`."""

    content_type: str
    filename: str
    body: bytes


_RECORD_COLUMNS = """
    ID AS id,
    PROJECT_ID AS project_id,
    DBMS_LOB.SUBSTR(DATA_JSON, 3500, 1) AS data_json,
    REVISION AS revision,
    CREATED_BY AS created_by,
    TO_CHAR(SYS_EXTRACT_UTC(CREATED_AT), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"') AS created_at,
    UPDATED_BY AS updated_by,
    TO_CHAR(SYS_EXTRACT_UTC(UPDATED_AT), 'YYYY-MM-DD"T"HH24:MI:SS.FF3"Z"') AS updated_at
"""


def _field(row: dict[str, Any], name: str) -> Any:
    """Read an Oracle result column despite driver casing differences."""
    if name in row:
        return row[name]
    upper = name.upper()
    if upper in row:
        return row[upper]
    lower = name.lower()
    if lower in row:
        return row[lower]
    return None


def _validate_json_value(value: Any, *, depth: int, nodes: list[int]) -> None:
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES:
        raise RecordValidationError(
            f"Record data can contain at most {MAX_JSON_NODES} JSON values."
        )
    if depth > MAX_JSON_DEPTH:
        raise RecordValidationError(
            f"Record data can be at most {MAX_JSON_DEPTH} levels deep."
        )

    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise RecordValidationError(
                f"Record strings can contain at most {MAX_STRING_CHARS} characters."
            )
        return
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise RecordValidationError("Record integers must be JavaScript-safe numbers.")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecordValidationError("Record numbers must be finite.")
        return
    if isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise RecordValidationError(
                f"Record arrays can contain at most {MAX_ARRAY_ITEMS} items."
            )
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_KEYS:
            raise RecordValidationError(
                f"Each record object can contain at most {MAX_OBJECT_KEYS} fields."
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > MAX_KEY_CHARS:
                raise RecordValidationError(
                    f"Record object keys must contain 1 to {MAX_KEY_CHARS} characters."
                )
            _validate_json_value(item, depth=depth + 1, nodes=nodes)
        return
    raise RecordValidationError("Record data may contain only JSON values.")


def _encode_data(data: Any) -> str:
    if not isinstance(data, dict):
        raise RecordValidationError("The record data field must be a JSON object.")
    _validate_json_value(data, depth=1, nodes=[0])
    try:
        encoded = json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        byte_count = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise RecordValidationError("Record data must be valid UTF-8 JSON.") from exc
    if byte_count > MAX_DATA_BYTES:
        raise RecordLimitError(
            f"A record can contain at most {MAX_DATA_BYTES} UTF-8 bytes.",
            details={"max_bytes": MAX_DATA_BYTES, "actual_bytes": byte_count},
        )
    return encoded


def _record_id(record_id: str) -> str:
    try:
        return str(UUID(record_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RecordValidationError("record_id must be a UUID.") from exc


def _normalise_record(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise RecordNotFoundError("Record was not found in this project.")
    raw_data = _field(row, "data_json")
    if not isinstance(raw_data, str):
        raise RecordStorageError("Stored record data could not be read.")
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        raise RecordStorageError("Stored record data is invalid JSON.") from exc
    return {
        "id": str(_field(row, "id")),
        "project_id": str(_field(row, "project_id")),
        "data": data,
        "revision": int(_field(row, "revision")),
        "created_by": str(_field(row, "created_by")),
        "created_at": str(_field(row, "created_at")),
        "updated_by": str(_field(row, "updated_by")),
        "updated_at": str(_field(row, "updated_at")),
    }


def _fetch_record(conn: Any, project_id: str, record_id: str) -> dict[str, Any] | None:
    return query_one(
        conn,
        f"SELECT {_RECORD_COLUMNS} FROM TB_TA_AGORA_RECORDS "
        "WHERE PROJECT_ID=:project_id AND ID=:record_id",
        {"project_id": project_id, "record_id": record_id},
    )


def _lock_record(conn: Any, project_id: str, record_id: str) -> dict[str, Any] | None:
    return query_one(
        conn,
        "SELECT ID AS id, REVISION AS revision FROM TB_TA_AGORA_RECORDS "
        "WHERE PROJECT_ID=:project_id AND ID=:record_id FOR UPDATE",
        {"project_id": project_id, "record_id": record_id},
    )


def _authorize(project_id: str, actor: Actor) -> str:
    return require_project_role(project_id, actor, minimum="viewer")


def _lock_project(conn: Any, project_id: str) -> dict[str, Any]:
    row = query_one(
        conn,
        "SELECT ID FROM TB_TA_AGORA_PROJECTS WHERE ID=:project_id FOR UPDATE",
        {"project_id": project_id},
    )
    if row is None:
        raise RecordNotFoundError("Project was not found.")
    return get_project(conn, project_id)


def _check_write_access(project: dict[str, Any], role: str) -> None:
    if role in {"owner", "editor", "admin"}:
        return
    if role == "viewer" and bool(project.get("allow_viewer_writes")):
        return
    raise RecordForbiddenError("Record writes are not enabled for this project role.")


def _record_count(conn: Any, project_id: str) -> int:
    row = query_one(
        conn,
        "SELECT COUNT(*) AS record_count FROM TB_TA_AGORA_RECORDS WHERE PROJECT_ID=:project_id",
        {"project_id": project_id},
    )
    return int(_field(row or {}, "record_count") or 0)


def _check_project_record_limit(conn: Any, project_id: str) -> None:
    count = _record_count(conn, project_id)
    if count > MAX_RECORDS_PER_PROJECT:
        raise RecordLimitError(
            "This project exceeds the saved-record limit; contact its owner.",
            details={"max_records": MAX_RECORDS_PER_PROJECT, "current_records": count},
        )


def _list_rows(conn: Any, project_id: str) -> list[dict[str, Any]]:
    _check_project_record_limit(conn, project_id)
    return query_all(
        conn,
        f"SELECT {_RECORD_COLUMNS} FROM TB_TA_AGORA_RECORDS "
        "WHERE PROJECT_ID=:project_id ORDER BY CREATED_AT, ID",
        {"project_id": project_id},
    )


def _check_revision(expected_revision: Any) -> int:
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        raise RecordValidationError("expected_revision must be a positive integer.")
    return expected_revision


def list_records(project_id: str, actor: Actor) -> list[dict[str, Any]]:
    """Return at most 500 records after checking project membership."""
    _authorize(project_id, actor)
    with connection() as conn:
        rows = _list_rows(conn, project_id)
    return [_normalise_record(row) for row in rows]


def get_record(project_id: str, record_id: str, actor: Actor) -> dict[str, Any]:
    """Return one record if the actor can view the project."""
    _authorize(project_id, actor)
    canonical_id = _record_id(record_id)
    with connection() as conn:
        row = _fetch_record(conn, project_id, canonical_id)
    return _normalise_record(row)


def create_record(project_id: str, actor: Actor, data: Any) -> dict[str, Any]:
    """Create a record with revision 1 and audit it in the same transaction."""
    role = _authorize(project_id, actor)
    encoded = _encode_data(data)
    record_id = str(uuid4())
    with transaction() as conn:
        project = _lock_project(conn, project_id)
        _check_write_access(project, role)
        count = _record_count(conn, project_id)
        if count >= MAX_RECORDS_PER_PROJECT:
            raise RecordLimitError(
                f"A project can contain at most {MAX_RECORDS_PER_PROJECT} saved records.",
                details={"max_records": MAX_RECORDS_PER_PROJECT, "current_records": count},
            )
        execute(
            conn,
            """
            INSERT INTO TB_TA_AGORA_RECORDS
                (ID, PROJECT_ID, DATA_JSON, REVISION, CREATED_BY, CREATED_AT,
                 UPDATED_BY, UPDATED_AT)
            VALUES
                (:record_id, :project_id, TO_CLOB(:data_json), 1, :actor_id,
                 SYSTIMESTAMP AT TIME ZONE 'UTC', :actor_id,
                 SYSTIMESTAMP AT TIME ZONE 'UTC')
            """,
            {
                "record_id": record_id,
                "project_id": project_id,
                "data_json": encoded,
                "actor_id": actor.id,
            },
        )
        audit(
            conn,
            actor.id,
            "record.created",
            project_id=project_id,
            subject_id=record_id,
            details={"revision": 1, "data_bytes": len(encoded.encode("utf-8"))},
        )
        row = _fetch_record(conn, project_id, record_id)
    return _normalise_record(row)


def update_record(
    project_id: str,
    record_id: str,
    actor: Actor,
    data: Any,
    expected_revision: int,
) -> dict[str, Any]:
    """Replace record data only when the caller's revision is current."""
    role = _authorize(project_id, actor)
    canonical_id = _record_id(record_id)
    expected = _check_revision(expected_revision)
    encoded = _encode_data(data)
    with transaction() as conn:
        project = _lock_project(conn, project_id)
        _check_write_access(project, role)
        row = _lock_record(conn, project_id, canonical_id)
        if row is None:
            raise RecordNotFoundError("Record was not found in this project.")
        current_revision = int(_field(row, "revision"))
        if current_revision != expected:
            raise RecordConflictError(
                "The record changed after it was loaded. Reload it before saving.",
                details={"expected_revision": expected, "current_revision": current_revision},
            )
        new_revision = current_revision + 1
        updated = execute(
            conn,
            """
            UPDATE TB_TA_AGORA_RECORDS
            SET DATA_JSON=TO_CLOB(:data_json), REVISION=:new_revision,
                UPDATED_BY=:actor_id, UPDATED_AT=SYSTIMESTAMP AT TIME ZONE 'UTC'
            WHERE PROJECT_ID=:project_id AND ID=:record_id AND REVISION=:expected_revision
            """,
            {
                "data_json": encoded,
                "new_revision": new_revision,
                "actor_id": actor.id,
                "project_id": project_id,
                "record_id": canonical_id,
                "expected_revision": expected,
            },
        )
        if updated != 1:
            latest = _fetch_record(conn, project_id, canonical_id)
            if latest is None:
                raise RecordNotFoundError("Record was not found in this project.")
            actual = int(_field(latest, "revision"))
            raise RecordConflictError(
                "The record changed after it was loaded. Reload it before saving.",
                details={"expected_revision": expected, "current_revision": actual},
            )
        audit(
            conn,
            actor.id,
            "record.updated",
            project_id=project_id,
            subject_id=canonical_id,
            details={
                "previous_revision": current_revision,
                "revision": new_revision,
                "data_bytes": len(encoded.encode("utf-8")),
            },
        )
        updated_row = _fetch_record(conn, project_id, canonical_id)
    return _normalise_record(updated_row)


def delete_record(
    project_id: str,
    record_id: str,
    actor: Actor,
    expected_revision: int,
) -> dict[str, Any]:
    """Delete a record only if its revision is current; retain an audit event."""
    role = _authorize(project_id, actor)
    canonical_id = _record_id(record_id)
    expected = _check_revision(expected_revision)
    with transaction() as conn:
        project = _lock_project(conn, project_id)
        _check_write_access(project, role)
        row = _lock_record(conn, project_id, canonical_id)
        if row is None:
            raise RecordNotFoundError("Record was not found in this project.")
        current_revision = int(_field(row, "revision"))
        if current_revision != expected:
            raise RecordConflictError(
                "The record changed after it was loaded. Reload it before deleting.",
                details={"expected_revision": expected, "current_revision": current_revision},
            )
        deleted = execute(
            conn,
            "DELETE FROM TB_TA_AGORA_RECORDS "
            "WHERE PROJECT_ID=:project_id AND ID=:record_id AND REVISION=:expected_revision",
            {
                "project_id": project_id,
                "record_id": canonical_id,
                "expected_revision": expected,
            },
        )
        if deleted != 1:
            latest = _fetch_record(conn, project_id, canonical_id)
            if latest is None:
                raise RecordNotFoundError("Record was not found in this project.")
            actual = int(_field(latest, "revision"))
            raise RecordConflictError(
                "The record changed after it was loaded. Reload it before deleting.",
                details={"expected_revision": expected, "current_revision": actual},
            )
        audit(
            conn,
            actor.id,
            "record.deleted",
            project_id=project_id,
            subject_id=canonical_id,
            details={"revision": current_revision},
        )
    return {"id": canonical_id, "deleted": True, "revision": current_revision}


def export_records(
    project_id: str,
    actor: Actor,
    format: ExportFormat | str = "json",
) -> RecordExport:
    """Export all project records after checking viewer membership."""
    _authorize(project_id, actor)
    if format not in {"json", "csv"}:
        raise RecordValidationError("format must be either 'json' or 'csv'.")
    with connection() as conn:
        rows = _list_rows(conn, project_id)
    records = [_normalise_record(row) for row in rows]
    safe_project_id = re.sub(r"[^A-Za-z0-9_-]", "", project_id)[:36] or "project"
    if format == "json":
        text = json.dumps(
            {"records": records}, ensure_ascii=False, separators=(",", ":")
        )
        return RecordExport(
            content_type="application/json; charset=utf-8",
            filename=f"project-{safe_project_id}-records.json",
            body=text.encode("utf-8"),
        )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "project_id", "revision", "created_by", "created_at", "updated_by", "updated_at", "data_json"]
    )
    for record in records:
        writer.writerow(
            [
                record["id"],
                record["project_id"],
                record["revision"],
                record["created_by"],
                record["created_at"],
                record["updated_by"],
                record["updated_at"],
                json.dumps(
                    record["data"], ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
            ]
        )
    return RecordExport(
        content_type="text/csv; charset=utf-8",
        filename=f"project-{safe_project_id}-records.csv",
        body=buffer.getvalue().encode("utf-8"),
    )
