"""Oracle persistence for immutable packages and working/published versions."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from agora.core.db import execute, query_all, query_one
from agora.content.bundle import ValidatedBundle


class ContentConflict(Exception):
    pass


def _id() -> str:
    return str(uuid.uuid4())


def _stamp(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "package_id": row["package_id"],
        "entry_path": row["entry_path"],
        "package_sha256": row["package_sha256"],
        "created_by": row["created_by"],
        "created_at": _stamp(row["created_at"]),
        "is_published": bool(row.get("is_published")),
    }


_VERSION_SELECT = """
SELECT v.id, v.project_id, v.package_id, p.entry_path, p.package_sha256,
       v.created_by, v.created_at,
       CASE WHEN pr.published_version_id = v.id THEN 1 ELSE 0 END AS is_published
FROM TB_TA_AGORA_CONTENT_VERSIONS v
JOIN TB_TA_AGORA_CONTENT_PACKAGES p ON p.id = v.package_id
JOIN TB_TA_AGORA_PROJECTS pr ON pr.id = v.project_id
"""


def get_version(conn: Any, project_id: str, version_id: str) -> dict[str, Any] | None:
    row = query_one(conn, _VERSION_SELECT + " WHERE v.project_id = :project_id AND v.id = :version_id",
                    {"project_id": project_id, "version_id": version_id})
    return _version(row) if row else None


def list_versions(conn: Any, project_id: str) -> list[dict[str, Any]]:
    rows = query_all(conn, _VERSION_SELECT + " WHERE v.project_id = :project_id ORDER BY v.created_at DESC, v.id DESC",
                     {"project_id": project_id})
    return [_version(row) for row in rows]


def published_version(conn: Any, project_id: str) -> dict[str, Any] | None:
    row = query_one(conn, _VERSION_SELECT + " WHERE v.project_id = :project_id AND pr.published_version_id = v.id",
                    {"project_id": project_id})
    return _version(row) if row else None


def create_package_version(conn: Any, project_id: str, actor_id: str, bundle: ValidatedBundle) -> str:
    package_id = _id()
    version_id = _id()
    digest = hashlib.sha256()
    for asset in bundle.assets:
        digest.update(asset.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset.sha256.encode("ascii"))
        digest.update(b"\0")
    execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_PACKAGES
        (id, project_id, entry_path, package_sha256, created_by)
        VALUES (:id, :project_id, :entry_path, :digest, :actor_id)""",
        {"id": package_id, "project_id": project_id, "entry_path": bundle.entry_path,
         "digest": digest.hexdigest(), "actor_id": actor_id})
    for asset in bundle.assets:
        execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_ASSETS
            (package_id, path, mime_type, sha256, byte_size, content)
            VALUES (:package_id, :path, :mime_type, :sha256, :byte_size, EMPTY_BLOB())""",
            {"package_id": package_id, "path": asset.path, "mime_type": asset.mime,
             "sha256": asset.sha256, "byte_size": len(asset.content)})
        with conn.cursor() as cursor:
            cursor.execute("""SELECT content FROM TB_TA_AGORA_CONTENT_ASSETS
                WHERE package_id = :package_id AND path = :path FOR UPDATE""",
                {"package_id": package_id, "path": asset.path})
            lob = cursor.fetchone()[0]
            if asset.content:
                lob.write(asset.content)
    execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_VERSIONS
        (id, project_id, package_id, created_by)
        VALUES (:id, :project_id, :package_id, :actor_id)""",
        {"id": version_id, "project_id": project_id, "package_id": package_id, "actor_id": actor_id})
    return version_id


def create_version_for_snapshot(conn: Any, project_id: str, source_version_id: str,
                                actor_id: str, snapshot_id: str) -> str:
    """Clone the HTML package pointer and bind a new immutable CSV snapshot."""
    source = get_version(conn, project_id, source_version_id)
    if source is None:
        raise ContentConflict("The source version was not found in this project.")
    version_id = _id()
    execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_VERSIONS
        (id, project_id, package_id, created_by)
        VALUES (:id, :project_id, :package_id, :actor_id)""",
        {"id": version_id, "project_id": project_id, "package_id": source["package_id"], "actor_id": actor_id})
    from agora.data.snapshots import bind_csv_snapshot
    bind_csv_snapshot(conn, version_id, snapshot_id)
    return version_id


def _lock_project(conn: Any, project_id: str) -> str | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT published_version_id FROM TB_TA_AGORA_PROJECTS WHERE id = :id FOR UPDATE", {"id": project_id})
        row = cursor.fetchone()
        if row is None:
            raise ContentConflict("Project no longer exists.")
        return row[0]


def _change_publication(conn: Any, project_id: str, actor_id: str, version_id: str,
                        previous_id: str | None, action: str) -> str:
    event_id = _id()
    execute(conn, """UPDATE TB_TA_AGORA_PROJECTS SET published_version_id = :version_id,
        updated_at = SYSTIMESTAMP WHERE id = :project_id""",
        {"version_id": version_id, "project_id": project_id})
    execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_PUBLICATIONS
        (id, project_id, version_id, previous_version_id, action, actor_id)
        VALUES (:id, :project_id, :version_id, :previous_id, :action, :actor_id)""",
        {"id": event_id, "project_id": project_id, "version_id": version_id,
         "previous_id": previous_id, "action": action, "actor_id": actor_id})
    event = query_one(conn, "SELECT created_at FROM TB_TA_AGORA_CONTENT_PUBLICATIONS WHERE id = :id", {"id": event_id})
    return _stamp(event["created_at"])


def publish(conn: Any, project_id: str, actor_id: str, version_id: str) -> dict[str, Any]:
    current = _lock_project(conn, project_id)
    selected = get_version(conn, project_id, version_id)
    if selected is None:
        raise ContentConflict("Version was not found in this project.")
    if current == version_id:
        raise ContentConflict("This version is already published.")
    published_at = _change_publication(conn, project_id, actor_id, version_id, current, "publish")
    return {"published_version_id": version_id, "previous_version_id": current, "published_at": published_at}


def rollback(conn: Any, project_id: str, actor_id: str) -> dict[str, Any]:
    current = _lock_project(conn, project_id)
    if not current:
        raise ContentConflict("Nothing is published yet.")
    row = query_one(conn, """SELECT previous_version_id FROM TB_TA_AGORA_CONTENT_PUBLICATIONS
        WHERE project_id = :project_id AND version_id = :current
        ORDER BY created_at DESC, ROWID DESC FETCH FIRST 1 ROW ONLY""",
        {"project_id": project_id, "current": current})
    previous = row["previous_version_id"] if row else None
    if not previous or get_version(conn, project_id, previous) is None:
        raise ContentConflict("No previous published version is available.")
    published_at = _change_publication(conn, project_id, actor_id, previous, current, "rollback")
    return {"published_version_id": previous, "previous_version_id": current, "published_at": published_at}


def latest_publication(conn: Any, project_id: str) -> dict[str, Any] | None:
    row = query_one(conn, """SELECT version_id, previous_version_id, action, created_at
        FROM TB_TA_AGORA_CONTENT_PUBLICATIONS WHERE project_id = :project_id
        ORDER BY created_at DESC, ROWID DESC FETCH FIRST 1 ROW ONLY""",
        {"project_id": project_id})
    if row is None:
        return None
    return {"published_version_id": row["version_id"],
            "previous_version_id": row["previous_version_id"],
            "action": row["action"], "published_at": _stamp(row["created_at"])}


def create_view_grant(conn: Any, project_id: str, version_id: str,
                      session_cookie: str) -> str:
    token = secrets.token_urlsafe(32)
    execute(conn, "DELETE FROM TB_TA_AGORA_CONTENT_VIEW_GRANTS WHERE expires_at <= SYSTIMESTAMP")
    execute(conn, """INSERT INTO TB_TA_AGORA_CONTENT_VIEW_GRANTS
        (token_hash, project_id, version_id, session_hash, expires_at)
        VALUES (:token_hash, :project_id, :version_id, :session_hash, :expires_at)""",
        {"token_hash": hashlib.sha256(token.encode()).hexdigest(), "project_id": project_id,
         "version_id": version_id,
         "session_hash": hashlib.sha256(session_cookie.encode()).hexdigest(),
         "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    return token


def get_view_grant(conn: Any, token: str) -> dict[str, Any] | None:
    return query_one(conn, """SELECT g.project_id, g.version_id, s.account_id
        FROM TB_TA_AGORA_CONTENT_VIEW_GRANTS g
        JOIN TB_TA_AGORA_SESSIONS s ON s.token_hash = g.session_hash
        WHERE g.token_hash = :token_hash AND g.expires_at > SYSTIMESTAMP
          AND s.expires_at > SYSTIMESTAMP""",
        {"token_hash": hashlib.sha256(token.encode()).hexdigest()})


def get_asset(conn: Any, project_id: str, version_id: str, path: str) -> dict[str, Any] | None:
    return query_one(conn, """SELECT a.path, a.mime_type, a.sha256, a.byte_size, a.content
        FROM TB_TA_AGORA_CONTENT_VERSIONS v
        JOIN TB_TA_AGORA_CONTENT_ASSETS a ON a.package_id = v.package_id
        WHERE v.project_id = :project_id AND v.id = :version_id AND a.path = :path""",
        {"project_id": project_id, "version_id": version_id, "path": path})
