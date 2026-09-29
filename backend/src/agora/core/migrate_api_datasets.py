"""Add API datasets to an existing Agora schema without changing existing data."""

from pathlib import Path
from sys import prefix

from agora.core.db import connection
from agora.core.schema import (
    _CONSTRAINT_NAMES, _statements, installed_constraints, installed_tables,
)

_SOURCE = Path(__file__).resolve().parents[3] / "migrations" / "001_api_datasets.sql"
_INSTALLED = Path(prefix) / "share" / "agora" / "migrations" / "001_api_datasets.sql"
MIGRATION_FILE = _SOURCE if _SOURCE.is_file() else _INSTALLED
TABLE = "TB_TA_AGORA_API_DATASETS"


def migrate() -> bool:
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    required = {name.upper() for name in _CONSTRAINT_NAMES.findall(source)}
    with connection() as conn:
        tables = installed_tables(conn)
        if TABLE in tables:
            missing = required - installed_constraints(conn)
            if missing:
                raise RuntimeError("API dataset schema is incomplete; review missing constraints: " + ", ".join(sorted(missing)))
            return False
        if not {"TB_TA_AGORA_PROJECTS", "TB_TA_AGORA_USERS"}.issubset(tables):
            raise RuntimeError("Install the baseline Agora schema before running this migration.")
        for statement in _statements(source):
            with conn.cursor() as cursor:
                cursor.execute(statement)
        conn.commit()
    return True


if __name__ == "__main__":
    print("Created API dataset table." if migrate() else "API dataset schema already exists.")
