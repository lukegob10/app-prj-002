"""Install durable scheduled API dataset refresh tables on an existing schema."""

from pathlib import Path
from sys import prefix

from agora.core.db import connection
from agora.core.schema import (
    _CONSTRAINT_NAMES,
    _INDEX_NAMES,
    _statements,
    installed_constraints,
    installed_indexes,
    installed_tables,
)


_SOURCE = Path(__file__).resolve().parents[3] / "migrations" / "002_api_dataset_schedules.sql"
_INSTALLED = Path(prefix) / "share" / "agora" / "migrations" / "002_api_dataset_schedules.sql"
MIGRATION_FILE = _SOURCE if _SOURCE.is_file() else _INSTALLED
SCHEDULE_TABLES = {
    "TB_TA_AGORA_API_DATASET_SCHEDULES",
    "TB_TA_AGORA_API_DATASET_RUNS",
}
BASE_TABLES = {
    "TB_TA_AGORA_PROJECTS",
    "TB_TA_AGORA_USERS",
    "TB_TA_AGORA_CONTENT_VERSIONS",
    "TB_TA_AGORA_CSV_SNAPSHOTS",
    "TB_TA_AGORA_API_DATASETS",
}


def migrate() -> bool:
    if not MIGRATION_FILE.is_file():
        raise RuntimeError(f"Schedule migration file was not found: {MIGRATION_FILE}")
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    statements = _statements(source)
    required_constraints = {name.upper() for name in _CONSTRAINT_NAMES.findall(source)}
    required_indexes = {name.upper() for name in _INDEX_NAMES.findall(source)}

    with connection() as conn:
        tables = installed_tables(conn)
        present_schedules = tables.intersection(SCHEDULE_TABLES)
        if present_schedules:
            if present_schedules != SCHEDULE_TABLES:
                missing_tables = ", ".join(sorted(SCHEDULE_TABLES - present_schedules))
                raise RuntimeError("API dataset scheduler schema is partial; missing tables: " + missing_tables)
            missing_constraints = required_constraints - installed_constraints(conn)
            missing_indexes = required_indexes - installed_indexes(conn)
            if missing_constraints or missing_indexes:
                details = []
                if missing_constraints:
                    details.append("constraints: " + ", ".join(sorted(missing_constraints)))
                if missing_indexes:
                    details.append("indexes: " + ", ".join(sorted(missing_indexes)))
                raise RuntimeError("API dataset scheduler schema is incomplete; review missing " + "; ".join(details))
            return False
        if not BASE_TABLES.issubset(tables):
            raise RuntimeError("Install the baseline Agora schema and API dataset migration before this migration.")

        constraints = installed_constraints(conn)
        # The baseline schema already has this composite key. Older databases
        # created through migration 001 need it before the scheduler FKs exist.
        for statement in statements:
            if "ALTER TABLE TB_TA_AGORA_API_DATASETS ADD CONSTRAINT UQ_TA_API_DATASET_PROJECT_ID" in statement.upper() and "UQ_TA_API_DATASET_PROJECT_ID" in constraints:
                continue
            with conn.cursor() as cursor:
                cursor.execute(statement)
        conn.commit()
    return True


if __name__ == "__main__":
    print("Created API dataset scheduler tables." if migrate() else "API dataset scheduler schema already exists.")
