"""Project access, membership administration, and audit helpers."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException

from agora.core.auth import Actor, require_actor, require_csrf
from agora.core.db import connection, execute, query_all, query_one, transaction


router = APIRouter(prefix="/api", tags=["projects"])

_PROJECT_FIELDS = """
    p.id AS id,
    p.name AS name,
    p.description AS description,
    p.owner_id AS owner_id,
    p.created_at AS created_at,
    p.updated_at AS updated_at,
    p.allow_viewer_writes AS allow_viewer_writes,
    p.published_version_id AS published_version_id
"""

_ROLE_ORDER = {"viewer": 0, "editor": 1, "owner": 2, "admin": 3}


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build an error that the app-level handler can put in the common envelope."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _project_shape(row: dict[str, Any], role: str | None = None) -> dict[str, Any]:
    project = {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "owner_id": row["owner_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "role": role if role is not None else row.get("role"),
        "allow_viewer_writes": bool(row.get("allow_viewer_writes", 0)),
        "published_version_id": row.get("published_version_id"),
    }
    return project


def get_project(conn: Any, project_id: str) -> dict[str, Any]:
    """Return a project row or raise 404. The returned mapping includes API fields."""
    row = query_one(
        conn,
        f"SELECT {_PROJECT_FIELDS} FROM TB_TA_AGORA_PROJECTS p WHERE p.id = :project_id",
        {"project_id": project_id},
    )
    if row is None:
        raise _http_error(404, "project_not_found", "Project was not found.")
    return _project_shape(row)


def require_project_role(project_id: str, actor: Actor, minimum: str = "viewer") -> str:
    """Check project membership and return the caller's effective role."""
    if minimum not in _ROLE_ORDER:
        raise ValueError(f"Unsupported project role requirement: {minimum}")

    with transaction() as conn:
        project = query_one(
            conn,
            "SELECT p.owner_id AS owner_id FROM TB_TA_AGORA_PROJECTS p WHERE p.id = :project_id",
            {"project_id": project_id},
        )
        if project is None:
            raise _http_error(404, "project_not_found", "Project was not found.")

        if getattr(actor, "is_admin", False):
            audit(
                conn,
                actor.id,
                "project.admin.override",
                project_id,
                details={"minimum_role": minimum},
            )
            return "admin"

        if project["owner_id"] == actor.id:
            role = "owner"
        else:
            membership = query_one(
                conn,
                """SELECT m.role AS role
                   FROM TB_TA_AGORA_MEMBERSHIPS m
                   WHERE m.project_id = :project_id AND m.account_id = :account_id""",
                {"project_id": project_id, "account_id": actor.id},
            )
            if membership is None:
                raise _http_error(
                    403, "project_forbidden", "You do not have access to this project."
                )
            role = str(membership["role"]).lower()

    if role not in _ROLE_ORDER:
        raise _http_error(403, "project_forbidden", "Your project role does not allow this action.")
    if _ROLE_ORDER[role] < _ROLE_ORDER[minimum]:
        raise _http_error(403, "project_forbidden", "Your project role does not allow this action.")
    return role


def audit(
    conn: Any,
    actor_id: str | None,
    action: str,
    project_id: str | None = None,
    subject_id: str | None = None,
    details: Any = None,
) -> None:
    """Insert an audit event into the caller's transaction."""
    encoded_details = None if details is None else json.dumps(
        details, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )
    execute(
        conn,
        """INSERT INTO TB_TA_AGORA_AUDIT
               (id, actor_id, action, project_id, subject_id, details)
           VALUES (:id, :actor_id, :action, :project_id, :subject_id, :details)""",
        {
            "id": str(uuid4()),
            "actor_id": actor_id,
            "action": action,
            "project_id": project_id,
            "subject_id": subject_id,
            "details": encoded_details,
        },
    )


def _validate_project_payload(payload: Any, *, patch: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _http_error(422, "validation_error", "Request body must be a JSON object.")

    allowed = {"name", "description"}
    if patch:
        allowed.add("allow_viewer_writes")
    extra = set(payload) - allowed
    if extra:
        raise _http_error(422, "validation_error", "Request contains unsupported project fields.")
    if not patch and "name" not in payload:
        raise _http_error(422, "validation_error", "Project name is required.")
    if patch and not payload:
        raise _http_error(422, "validation_error", "At least one project field must be provided.")

    clean: dict[str, Any] = {}
    if "name" in payload:
        name = payload["name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            raise _http_error(422, "validation_error", "Project name must be 1 to 160 characters.")
        clean["name"] = name.strip()
    if "description" in payload:
        description = payload["description"]
        if description is not None and (
            not isinstance(description, str) or len(description) > 1000
        ):
            raise _http_error(
                422,
                "validation_error",
                "Project description must be at most 1000 characters.",
            )
        clean["description"] = description.strip() if isinstance(description, str) else None
    if "allow_viewer_writes" in payload:
        enabled = payload["allow_viewer_writes"]
        if not isinstance(enabled, bool):
            raise _http_error(422, "validation_error", "allow_viewer_writes must be a boolean.")
        clean["allow_viewer_writes"] = enabled
    return clean


def _username_norm(username: str) -> str:
    normalized = username.strip().casefold()
    if not normalized or len(username) > 64:
        raise _http_error(422, "validation_error", "Username must be 1 to 64 characters.")
    return normalized


@router.get("/projects")
def list_projects(actor: Actor = Depends(require_actor)) -> dict[str, list[dict[str, Any]]]:
    with transaction() as conn:
        rows = query_all(
            conn,
            f"""SELECT {_PROJECT_FIELDS},
                       CASE
                           WHEN p.owner_id = :actor_id THEN 'owner'
                           WHEN :is_admin = 1 THEN 'admin'
                           ELSE m.role
                       END AS role
                FROM TB_TA_AGORA_PROJECTS p
                LEFT JOIN TB_TA_AGORA_MEMBERSHIPS m
                    ON m.project_id = p.id AND m.account_id = :actor_id
                WHERE p.owner_id = :actor_id
                   OR m.account_id = :actor_id
                   OR :is_admin = 1
                ORDER BY p.updated_at DESC, p.name ASC""",
            {"actor_id": actor.id, "is_admin": 1 if getattr(actor, "is_admin", False) else 0},
        )
        if getattr(actor, "is_admin", False):
            audit(conn, actor.id, "admin.projects.list", details={"count": len(rows)})
    owned: list[dict[str, Any]] = []
    shared: list[dict[str, Any]] = []
    for row in rows:
        project = _project_shape(row, str(row["role"]).lower())
        (owned if project["role"] == "owner" else shared).append(project)
    return {"owned": owned, "shared": shared}


@router.post("/projects")
def create_project(
    payload: dict[str, Any] = Body(...), actor: Actor = Depends(require_csrf)
) -> dict[str, Any]:
    values = _validate_project_payload(payload, patch=False)
    project_id = str(uuid4())
    with transaction() as conn:
        execute(
            conn,
            """INSERT INTO TB_TA_AGORA_PROJECTS
                   (id, name, description, owner_id, allow_viewer_writes, published_version_id)
               VALUES (:id, :name, :description, :owner_id, 0, NULL)""",
            {
                "id": project_id,
                "name": values["name"],
                "description": values.get("description"),
                "owner_id": actor.id,
            },
        )
        execute(
            conn,
            """INSERT INTO TB_TA_AGORA_MEMBERSHIPS (project_id, account_id, role)
               VALUES (:project_id, :account_id, 'owner')""",
            {"project_id": project_id, "account_id": actor.id},
        )
        audit(conn, actor.id, "project.create", project_id, details={"name": values["name"]})
        project = get_project(conn, project_id)
    project["role"] = "owner"
    return project


@router.get("/projects/{project_id}")
def read_project(project_id: str, actor: Actor = Depends(require_actor)) -> dict[str, Any]:
    role = require_project_role(project_id, actor, "viewer")
    with connection() as conn:
        project = get_project(conn, project_id)
    project["role"] = role
    return project


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "owner")
    values = _validate_project_payload(payload, patch=True)
    assignments: list[str] = []
    params: dict[str, Any] = {"project_id": project_id}
    for field in ("name", "description", "allow_viewer_writes"):
        if field in values:
            assignments.append(f"{field} = :{field}")
            params[field] = int(values[field]) if field == "allow_viewer_writes" else values[field]
    assignments.append("updated_at = SYSTIMESTAMP")

    with transaction() as conn:
        updated = execute(
            conn,
            f"UPDATE TB_TA_AGORA_PROJECTS SET {', '.join(assignments)} WHERE id = :project_id",
            params,
        )
        if not updated:
            raise _http_error(404, "project_not_found", "Project was not found.")
        audit(conn, actor.id, "project.update", project_id, details=values)
        project = get_project(conn, project_id)
    project["role"] = "admin" if getattr(actor, "is_admin", False) else "owner"
    return project


@router.get("/projects/{project_id}/members")
def list_members(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "owner")
    with connection() as conn:
        members = query_all(
            conn,
            """SELECT m.account_id AS account_id, a.username AS username,
                      a.full_name AS full_name, m.role AS role
               FROM TB_TA_AGORA_MEMBERSHIPS m
               JOIN TB_TA_AGORA_USERS a ON a.id = m.account_id
               WHERE m.project_id = :project_id
               ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                        a.username_norm""",
            {"project_id": project_id},
        )
    return {"members": members}


@router.put("/projects/{project_id}/members/{username}")
def put_member(
    project_id: str,
    username: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "owner")
    if not isinstance(payload, dict) or set(payload) != {"role"}:
        raise _http_error(422, "validation_error", 'Request body must contain only "role".')
    role = payload["role"]
    if not isinstance(role, str) or role not in {"editor", "viewer"}:
        raise _http_error(422, "validation_error", 'Member role must be "editor" or "viewer".')
    normalized_username = _username_norm(username)
    with transaction() as conn:
        project = get_project(conn, project_id)
        account = query_one(
            conn,
            """SELECT a.id AS id, a.username AS username
               FROM TB_TA_AGORA_USERS a WHERE a.username_norm = :username_norm""",
            {"username_norm": normalized_username},
        )
        if account is None:
            raise _http_error(404, "account_not_found", "Account was not found.")
        if account["id"] == project["owner_id"]:
            raise _http_error(
                409,
                "owner_membership_immutable",
                "The project owner cannot be changed as a member.",
            )
        execute(
            conn,
            """MERGE INTO TB_TA_AGORA_MEMBERSHIPS m
               USING (SELECT :project_id AS project_id, :account_id AS account_id FROM dual) src
                  ON (m.project_id = src.project_id AND m.account_id = src.account_id)
               WHEN MATCHED THEN UPDATE SET m.role = :role
               WHEN NOT MATCHED THEN INSERT (project_id, account_id, role)
                    VALUES (src.project_id, src.account_id, :role)""",
            {"project_id": project_id, "account_id": account["id"], "role": role},
        )
        audit(
            conn,
            actor.id,
            "project.member.upsert",
            project_id,
            subject_id=account["id"],
            details={"username": account["username"], "role": role},
        )
        member = query_one(
            conn,
            """SELECT m.account_id AS account_id, a.username AS username,
                      a.full_name AS full_name, m.role AS role
               FROM TB_TA_AGORA_MEMBERSHIPS m JOIN TB_TA_AGORA_USERS a ON a.id = m.account_id
               WHERE m.project_id = :project_id AND m.account_id = :account_id""",
            {"project_id": project_id, "account_id": account["id"]},
        )
    return member or {}


@router.delete("/projects/{project_id}/members/{username}")
def delete_member(
    project_id: str, username: str, actor: Actor = Depends(require_csrf)
) -> dict[str, bool]:
    require_project_role(project_id, actor, "owner")
    normalized_username = _username_norm(username)
    with transaction() as conn:
        project = get_project(conn, project_id)
        account = query_one(
            conn,
            """SELECT a.id AS id, a.username AS username
               FROM TB_TA_AGORA_USERS a WHERE a.username_norm = :username_norm""",
            {"username_norm": normalized_username},
        )
        if account is None:
            raise _http_error(404, "account_not_found", "Account was not found.")
        if account["id"] == project["owner_id"]:
            raise _http_error(
                409,
                "owner_membership_immutable",
                "The project owner cannot be removed as a member.",
            )
        removed = execute(
            conn,
            """DELETE FROM TB_TA_AGORA_MEMBERSHIPS
               WHERE project_id = :project_id AND account_id = :account_id""",
            {"project_id": project_id, "account_id": account["id"]},
        )
        if not removed:
            raise _http_error(404, "membership_not_found", "Project membership was not found.")
        audit(
            conn,
            actor.id,
            "project.member.remove",
            project_id,
            subject_id=account["id"],
            details={"username": account["username"]},
        )
    return {"removed": True}
