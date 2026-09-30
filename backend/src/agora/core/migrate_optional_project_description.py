"""Allow projects without descriptions in existing Agora databases."""

from pathlib import Path
from sys import prefix

from agora.core.db import connection, query_one
from agora.core.schema import _statements


_SOURCE = Path(__file__).resolve().parents[3] / "migrations" / "003_optional_project_description.sql"
_INSTALLED = Path(prefix) / "share" / "agora" / "migrations" / "003_optional_project_description.sql"
MIGRATION_FILE = _SOURCE if _SOURCE.is_file() else _INSTALLED


def migrate() -> bool:
    with connection() as conn:
        column = query_one(
            conn,
            "SELECT nullable FROM user_tab_columns "
            "WHERE table_name = 'TB_TA_AGORA_PROJECTS' AND column_name = 'DESCRIPTION'",
        )
        if column is None:
            raise RuntimeError("Install the baseline Agora schema before running this migration.")
        if column["nullable"] == "Y":
            return False
        for statement in _statements(MIGRATION_FILE.read_text(encoding="utf-8")):
            with conn.cursor() as cursor:
                cursor.execute(statement)
    return True


if __name__ == "__main__":
    print("Project descriptions are optional." if migrate() else "Project descriptions are already optional.")
