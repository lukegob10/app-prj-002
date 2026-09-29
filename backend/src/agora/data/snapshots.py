"""Validated, immutable CSV snapshots and exact content-version bindings."""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import uuid
from dataclasses import dataclass
from typing import Any

from agora.core.db import query_all, query_one


MAX_CSV_BYTES = 8 * 1024 * 1024
MAX_CSV_ROWS = 100_000
MAX_CSV_COLUMNS = 100
MAX_COLUMN_CHARS = 128
MAX_CELL_CHARS = 32_000
DEFAULT_PREVIEW_PAGE_SIZE = 10


@dataclass(frozen=True)
class ValidatedCSV:
    payload: bytes
    filename: str
    columns: list[str]
    row_count: int
    sha256: str


class CSVValidationError(ValueError):
    """A CSV does not fit the bounded, tabular input contract."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def validate_csv(filename: str, payload: bytes) -> ValidatedCSV:
    if not filename or not filename.lower().endswith(".csv"):
        raise CSVValidationError("Choose a .csv file.")
    clean_filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean_filename or len(clean_filename.encode("utf-8")) > 255 or any(ord(char) < 32 for char in clean_filename):
        raise CSVValidationError("CSV filename must be at most 255 UTF-8 bytes and contain no control characters.")
    if not payload:
        raise CSVValidationError("CSV file is empty.")
    if len(payload) > MAX_CSV_BYTES:
        raise CSVValidationError("CSV exceeds the 8 MiB limit.", details={"max_bytes": MAX_CSV_BYTES})
    try:
        decoded = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise CSVValidationError("CSV must use UTF-8 text.") from exc
    if "\x00" in decoded:
        raise CSVValidationError("CSV contains a NUL character.")

    try:
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        columns = next(reader, None)
        if not columns:
            raise CSVValidationError("CSV needs a header row.")
        if len(columns) > MAX_CSV_COLUMNS:
            raise CSVValidationError(
                "CSV has too many columns.", details={"max_columns": MAX_CSV_COLUMNS}
            )
        if any(not name.strip() or len(name) > MAX_COLUMN_CHARS for name in columns):
            raise CSVValidationError("CSV headers must be nonempty and at most 128 characters.")
        if len({name.casefold() for name in columns}) != len(columns):
            raise CSVValidationError("CSV headers must be unique, ignoring case.")
        if any(any(ord(char) < 32 for char in name) for name in columns):
            raise CSVValidationError("CSV headers cannot contain control characters.")
        row_count = 0
        for row in reader:
            row_count += 1
            if row_count > MAX_CSV_ROWS:
                raise CSVValidationError(
                    "CSV has too many data rows.", details={"max_rows": MAX_CSV_ROWS}
                )
            if len(row) != len(columns):
                raise CSVValidationError(
                    f"Row {row_count + 1} has {len(row)} cells; expected {len(columns)}.",
                    details={"row": row_count + 1, "expected_columns": len(columns)},
                )
            if any(len(cell) > MAX_CELL_CHARS for cell in row):
                raise CSVValidationError(
                    f"Row {row_count + 1} has a cell over 32,000 characters.",
                    details={"row": row_count + 1, "max_cell_chars": MAX_CELL_CHARS},
                )
    except csv.Error as exc:
        raise CSVValidationError(f"Malformed CSV: {exc}") from exc

    return ValidatedCSV(
        payload=payload,
        filename=clean_filename,
        columns=columns,
        row_count=row_count,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def create_csv_snapshot(
    conn: Any, project_id: str, actor_id: str, filename: str, payload: bytes
) -> str:
    """Store an immutable snapshot inside the caller's Oracle transaction."""
    csv_input = validate_csv(filename, payload)
    snapshot_id = str(uuid.uuid4())
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO TB_TA_AGORA_CSV_SNAPSHOTS
                 (id, project_id, filename, content_bytes, sha256, byte_size,
                  row_count, columns_json, created_by, created_at)
               VALUES
                 (:id, :project_id, :filename, EMPTY_BLOB(), :sha256, :byte_size,
                  :row_count, :columns_json, :created_by, SYSTIMESTAMP)""",
            {
                "id": snapshot_id,
                "project_id": project_id,
                "filename": csv_input.filename,
                "sha256": csv_input.sha256,
                "byte_size": len(csv_input.payload),
                "row_count": csv_input.row_count,
                "columns_json": json.dumps(csv_input.columns, ensure_ascii=False),
                "created_by": actor_id,
            },
        )
        cursor.execute(
            "SELECT content_bytes FROM TB_TA_AGORA_CSV_SNAPSHOTS WHERE id = :id FOR UPDATE",
            {"id": snapshot_id},
        )
        lob = cursor.fetchone()[0]
        lob.write(csv_input.payload)
    return snapshot_id


def bind_csv_snapshot(conn: Any, version_id: str, snapshot_id: str) -> None:
    """Bind once, rejecting cross-project and missing-version associations."""
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO TB_TA_AGORA_VERSION_CSV_BINDINGS
                   (project_id, version_id, snapshot_id)
               SELECT v.project_id, v.id, s.id
               FROM TB_TA_AGORA_CONTENT_VERSIONS v
               JOIN TB_TA_AGORA_CSV_SNAPSHOTS s ON s.project_id = v.project_id
               WHERE v.id = :version_id AND s.id = :snapshot_id""",
            {"version_id": version_id, "snapshot_id": snapshot_id},
        )
        if cursor.rowcount != 1:
            raise ValueError("Content version and CSV snapshot must exist in the same project.")


def get_bound_snapshot_id(conn: Any, version_id: str) -> str | None:
    row = query_one(
        conn,
        "SELECT snapshot_id FROM TB_TA_AGORA_VERSION_CSV_BINDINGS WHERE version_id = :version_id",
        {"version_id": version_id},
    )
    return row["snapshot_id"] if row else None


def list_csv_snapshots(conn: Any, project_id: str) -> list[dict[str, Any]]:
    rows = query_all(
        conn,
        """SELECT id, project_id, filename, sha256, byte_size, row_count,
                  columns_json, created_by, created_at
           FROM TB_TA_AGORA_CSV_SNAPSHOTS
           WHERE project_id = :project_id
           ORDER BY created_at DESC, id DESC""",
        {"project_id": project_id},
    )
    return [_metadata(row) for row in rows]


def get_csv_snapshot(conn: Any, project_id: str, snapshot_id: str) -> dict[str, Any] | None:
    row = query_one(
        conn,
        """SELECT id, project_id, filename, sha256, byte_size, row_count,
                  columns_json, created_by, created_at
           FROM TB_TA_AGORA_CSV_SNAPSHOTS
           WHERE project_id = :project_id AND id = :snapshot_id""",
        {"project_id": project_id, "snapshot_id": snapshot_id},
    )
    return _metadata(row) if row else None


def read_csv_snapshot(conn: Any, project_id: str, snapshot_id: str) -> bytes | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT content_bytes FROM TB_TA_AGORA_CSV_SNAPSHOTS
               WHERE project_id = :project_id AND id = :snapshot_id""",
            {"project_id": project_id, "snapshot_id": snapshot_id},
        )
        row = cursor.fetchone()
        if row is None:
            return None
        value = row[0]
        return value.read() if hasattr(value, "read") else bytes(value)


def preview_csv_snapshot(
    conn: Any, project_id: str, snapshot_id: str, page: int = 1,
    page_size: int = DEFAULT_PREVIEW_PAGE_SIZE,
) -> dict[str, Any] | None:
    metadata = get_csv_snapshot(conn, project_id, snapshot_id)
    if metadata is None:
        return None
    payload = read_csv_snapshot(conn, project_id, snapshot_id)
    if payload is None:
        return None
    reader = csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
    next(reader)  # Every stored CSV was validated with a header row.
    start = (page - 1) * page_size
    return {
        "columns": metadata["columns"],
        "rows": list(itertools.islice(reader, start, start + page_size)),
        "row_count": metadata["row_count"],
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (metadata["row_count"] + page_size - 1) // page_size),
    }


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    columns_json = result.pop("columns_json")
    # Oracle may materialize an IS JSON CLOB as a native Python list.
    if hasattr(columns_json, "read"):
        columns_json = columns_json.read()
    result["columns"] = columns_json if isinstance(columns_json, list) else json.loads(columns_json)
    result["created_at"] = result["created_at"].isoformat()
    return result
