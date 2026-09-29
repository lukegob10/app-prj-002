"""Create Agora's initial Oracle schema from the single baseline SQL file."""

from pathlib import Path
import re
from sys import prefix

from agora.core.db import connection, query_all


_SOURCE_SCHEMA = Path(__file__).resolve().parents[3] / "schema.sql"
_INSTALLED_SCHEMA = Path(prefix) / "share" / "agora" / "schema.sql"
SCHEMA_FILE = _SOURCE_SCHEMA if _SOURCE_SCHEMA.is_file() else _INSTALLED_SCHEMA
APP_TABLES = (
    "TB_TA_AGORA_USERS",
    "TB_TA_AGORA_SESSIONS",
    "TB_TA_AGORA_LOGIN_ATTEMPTS",
    "TB_TA_AGORA_PROJECTS",
    "TB_TA_AGORA_MEMBERSHIPS",
    "TB_TA_AGORA_AUDIT",
    "TB_TA_AGORA_CONTENT_PACKAGES",
    "TB_TA_AGORA_CONTENT_ASSETS",
    "TB_TA_AGORA_CONTENT_VERSIONS",
    "TB_TA_AGORA_CONTENT_PUBLICATIONS",
    "TB_TA_AGORA_CONTENT_VIEW_GRANTS",
    "TB_TA_AGORA_CSV_SNAPSHOTS",
    "TB_TA_AGORA_VERSION_CSV_BINDINGS",
    "TB_TA_AGORA_RECORDS",
    "TB_TA_AGORA_DATA_SOURCES",
    "TB_TA_AGORA_PROJECT_SOURCES",
    "TB_TA_AGORA_API_DATASETS",
    "TB_TA_AGORA_API_DATASET_SCHEDULES",
    "TB_TA_AGORA_API_DATASET_RUNS",
)
_CONSTRAINT_NAMES = re.compile(r"\bCONSTRAINT\s+([A-Z][A-Z0-9_$#]*)", re.IGNORECASE)
_INDEX_NAMES = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+([A-Z][A-Z0-9_$#]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _statements(source: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in source.splitlines():
        if line.strip() == "/":
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(line)
    if any(line.strip() for line in current):
        raise ValueError("Schema SQL must end each statement with a '/' line")
    return statements


def installed_tables(conn) -> set[str]:
    """Return Agora table names visible in the session's current schema."""
    rows = query_all(
        conn,
        "SELECT table_name FROM all_tables "
        "WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
        "AND SUBSTR(table_name, 1, 12) = 'TB_TA_AGORA_'",
    )
    return {row["table_name"].upper() for row in rows}


def installed_constraints(conn) -> set[str]:
    """Return enabled and validated constraint names in the current schema."""
    rows = query_all(
        conn,
        "SELECT constraint_name FROM all_constraints "
        "WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
        "AND status = 'ENABLED' AND validated = 'VALIDATED'",
    )
    return {row["constraint_name"].upper() for row in rows}


def installed_indexes(conn) -> set[str]:
    """Return usable index names in the current schema."""
    rows = query_all(
        conn,
        "SELECT index_name FROM all_indexes "
        "WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
        "AND status = 'VALID'",
    )
    return {row["index_name"].upper() for row in rows}


def missing_schema_objects(conn) -> dict[str, set[str]]:
    """Compare required tables, constraints, and explicit indexes to the baseline."""
    if not SCHEMA_FILE.is_file():
        raise RuntimeError(f"Schema file was not found: {SCHEMA_FILE}")
    source = SCHEMA_FILE.read_text(encoding="utf-8")
    constraints = {name.upper() for name in _CONSTRAINT_NAMES.findall(source)}
    indexes = {name.upper() for name in _INDEX_NAMES.findall(source)}
    return {
        "tables": set(APP_TABLES).difference(installed_tables(conn)),
        "constraints": constraints.difference(installed_constraints(conn)),
        "indexes": indexes.difference(installed_indexes(conn)),
    }


def installed_legacy_tables(conn) -> set[str]:
    """Return tables left under Agora's earlier table-name prefixes."""
    rows = query_all(
        conn,
        "SELECT table_name FROM all_tables "
        "WHERE owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') "
        "AND (SUBSTR(table_name, 1, 6) = 'AGORA_' "
        "OR SUBSTR(table_name, 1, 11) = 'TBTA_AGORA_')",
    )
    return {row["table_name"].upper() for row in rows}


def apply_schema() -> list[str]:
    """Install the baseline schema once into the connected user's default schema."""
    if not SCHEMA_FILE.is_file():
        raise RuntimeError(f"Schema file was not found: {SCHEMA_FILE}")
    source = SCHEMA_FILE.read_text(encoding="utf-8")
    statements = _statements(source)

    with connection() as conn:
        discovered = installed_tables(conn)
        legacy = installed_legacy_tables(conn)
        expected = set(APP_TABLES)
        existing = discovered.intersection(expected)
        missing = set(APP_TABLES).difference(existing)
        unexpected = discovered.difference(expected)
        source_constraints = {name.upper() for name in _CONSTRAINT_NAMES.findall(source)}
        source_indexes = {name.upper() for name in _INDEX_NAMES.findall(source)}
        present_constraints = installed_constraints(conn)
        present_indexes = installed_indexes(conn)
        missing_constraints = source_constraints.difference(present_constraints)
        missing_indexes = source_indexes.difference(present_indexes)
        if legacy:
            names = ", ".join(sorted(legacy))
            raise RuntimeError(
                "The default schema contains tables from an earlier Agora naming scheme. "
                "The baseline will not drop or rename them. Review and remove "
                f"the old objects deliberately before setup. Found: {names}."
            )
        if not missing and not unexpected and not missing_constraints and not missing_indexes:
            return []
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise RuntimeError(
                "The default schema contains unrecognized or legacy TB_TA_AGORA tables. "
                "The baseline will not drop or overwrite them. Review and remove "
                f"the old objects deliberately before setup. Found: {names}."
            )
        has_schema_objects = bool(discovered) or bool(source_constraints.intersection(present_constraints)) or bool(
            source_indexes.intersection(present_indexes)
        )
        if has_schema_objects:
            names = ", ".join(sorted(missing)) or "none"
            missing_items = []
            if missing_constraints:
                missing_items.append("constraints: " + ", ".join(sorted(missing_constraints)))
            if missing_indexes:
                missing_items.append("indexes: " + ", ".join(sorted(missing_indexes)))
            details = "; ".join(missing_items) or "none"
            raise RuntimeError(
                "The default schema contains a partial or drifted Agora schema; refusing to change it. "
                f"Missing tables: {names}; missing required {details}. "
                "Review backend/schema.sql before continuing."
            )

        for statement in statements:
            with conn.cursor() as cursor:
                cursor.execute(statement)
        conn.commit()
    return list(APP_TABLES)


if __name__ == "__main__":
    created = apply_schema()
    print("Created Agora tables:", ", ".join(created) if created else "schema already exists")
