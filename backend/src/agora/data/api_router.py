"""Editor-only saved HTTPS API configuration and versioned import endpoints."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from agora.core.auth import Actor, require_actor, require_csrf
from agora.core.db import connection, transaction
from agora.core.projects import audit, require_project_role
from agora.data import api_datasets, snapshots
from agora.data import scheduler


router = APIRouter(prefix="/api", tags=["api-datasets"])


def _error(status: int, code: str, message: str, details: Any = None) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=status, detail=detail)


def _api_call(operation: Any, *args: Any) -> Any:
    try:
        return operation(*args)
    except api_datasets.ApiDatasetError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


def _schedule_call(operation: Any, *args: Any) -> Any:
    try:
        return operation(*args)
    except scheduler.ScheduleError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc


def _schedule_changed(clean: dict[str, Any], existing: dict[str, Any]) -> bool:
    fields = (
        "frequency", "interval_minutes", "local_time", "weekdays", "day_of_month",
        "timezone", "base_version_id", "publish_mode",
    )
    return any(clean.get(field) != existing.get(field) for field in fields)


def _snapshot_filename(name: str, connection_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip(".-_")[:80] or "dataset"
    return f"api-{slug}-{connection_id[:8]}.csv"


@router.get("/projects/{project_id}/api-datasets")
def list_api_datasets(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "editor")
    return {"connections": _api_call(api_datasets.list_connections, project_id)}


@router.post("/projects/{project_id}/api-datasets", status_code=201)
def create_api_dataset(
    project_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, dict[str, Any]]:
    require_project_role(project_id, actor, "editor")
    config = _api_call(api_datasets.validate_config, payload)
    try:
        item = api_datasets.create_connection(project_id, actor.id, payload)
    except api_datasets.ApiDatasetError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    with transaction() as conn:
        audit(conn, actor.id, "api_dataset.created", project_id, item["id"])
    return {"connection": item}


@router.put("/projects/{project_id}/api-datasets/{connection_id}")
def update_api_dataset(
    project_id: str,
    connection_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, dict[str, Any]]:
    require_project_role(project_id, actor, "editor")
    try:
        item = api_datasets.update_connection(project_id, connection_id, payload)
    except api_datasets.ApiDatasetError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    if item is None:
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    with transaction() as conn:
        audit(conn, actor.id, "api_dataset.updated", project_id, connection_id)
    return {"connection": item}


@router.delete("/projects/{project_id}/api-datasets/{connection_id}")
def delete_api_dataset(
    project_id: str,
    connection_id: str,
    actor: Actor = Depends(require_csrf),
) -> dict[str, bool]:
    require_project_role(project_id, actor, "editor")
    deleted = api_datasets.delete_connection(project_id, connection_id)
    if not deleted:
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    with transaction() as conn:
        audit(conn, actor.id, "api_dataset.deleted", project_id, connection_id)
    return {"deleted": True}


@router.get("/projects/{project_id}/api-datasets/{connection_id}/schedule")
def get_api_dataset_schedule(
    project_id: str, connection_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    if not scheduler.connection_exists(project_id, connection_id):
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    return {"schedule": _schedule_call(scheduler.get_schedule, project_id, connection_id)}


@router.put("/projects/{project_id}/api-datasets/{connection_id}/schedule")
def put_api_dataset_schedule(
    project_id: str,
    connection_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    role = require_project_role(project_id, actor, "editor")
    clean = _schedule_call(scheduler.validate_schedule, payload)
    existing = _schedule_call(scheduler.get_schedule, project_id, connection_id)
    if not scheduler.connection_exists(project_id, connection_id):
        raise _error(404, "api_connection_not_found", "API connection was not found.")

    if clean["publish_mode"] == "auto_publish" and role not in {"owner", "admin"}:
        no_auto_change = existing is not None and existing["publish_mode"] == "auto_publish" and not _schedule_changed(clean, existing)
        pause_only = no_auto_change and existing["enabled"] and not clean["enabled"]
        no_op = no_auto_change and existing["enabled"] == clean["enabled"]
        if not (pause_only or no_op):
            raise _error(403, "auto_publish_owner_required", "Only a project owner or admin can enable or change automatic publishing.")

    try:
        schedule = scheduler.save_schedule(project_id, connection_id, actor.id, payload, role)
    except scheduler.ScheduleError as exc:
        raise _error(exc.status_code, exc.code, exc.message) from exc
    if schedule is None:
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    with transaction() as conn:
        audit(
            conn, actor.id, "api_dataset.schedule_updated", project_id, connection_id,
            {"enabled": schedule["enabled"], "frequency": schedule["frequency"],
             "publish_mode": schedule["publish_mode"], "timezone": schedule["timezone"]},
        )
    return {"schedule": schedule}


@router.get("/projects/{project_id}/api-datasets/{connection_id}/runs")
def list_api_dataset_runs(
    project_id: str,
    connection_id: str,
    limit: int = Query(default=20, ge=1, le=50),
    actor: Actor = Depends(require_actor),
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "editor")
    if not scheduler.connection_exists(project_id, connection_id):
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    return {"runs": _schedule_call(scheduler.list_runs, project_id, connection_id, limit)}


@router.post("/projects/{project_id}/api-datasets/{connection_id}/run-now", status_code=202)
def run_api_dataset_now(
    project_id: str,
    connection_id: str,
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    role = require_project_role(project_id, actor, "editor")
    if not scheduler.connection_exists(project_id, connection_id):
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    schedule = _schedule_call(scheduler.get_schedule, project_id, connection_id)
    if schedule is None:
        raise _error(404, "api_schedule_not_found", "Create a schedule before running this connection.")
    if schedule["publish_mode"] == "auto_publish" and role not in {"owner", "admin"}:
        raise _error(403, "auto_publish_owner_required", "Only a project owner or admin can run an automatic publish now.")
    return {"run": _schedule_call(scheduler.queue_manual_run, project_id, connection_id, actor.id, role)}


@router.post("/projects/{project_id}/api-datasets/preview")
def preview_api_dataset(
    project_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    config = _api_call(api_datasets.validate_config, payload)
    return _api_call(api_datasets.preview_config, config)


@router.post("/projects/{project_id}/api-datasets/{connection_id}/test")
def test_api_dataset(
    project_id: str,
    connection_id: str,
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    result = _api_call(api_datasets.test_connection, project_id, connection_id)
    if result is None:
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    return result


@router.post("/projects/{project_id}/api-datasets/{connection_id}/import")
def import_api_dataset(
    project_id: str,
    connection_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    base_version_id = payload.get("base_version_id")
    if not isinstance(base_version_id, str) or not base_version_id.strip():
        raise _error(422, "invalid_base_version", "Choose an HTML version to create the imported snapshot from.")

    from agora.content.repository import ContentConflict, create_version_for_snapshot, get_version

    # Fail before making a remote request if the selected HTML package is unavailable.
    with connection() as conn:
        source_version = get_version(conn, project_id, base_version_id)
    if source_version is None:
        raise _error(404, "version_not_found", "The selected HTML version was not found in this project.")

    config = _api_call(api_datasets.get_config_for_project, project_id, connection_id)
    if config is None:
        raise _error(404, "api_connection_not_found", "API connection was not found.")
    csv_payload, _, _ = _api_call(api_datasets.response_to_csv, config)

    try:
        with transaction() as conn:
            snapshot_id = snapshots.create_csv_snapshot(
                conn,
                project_id,
                actor.id,
                _snapshot_filename(config["name"], connection_id),
                csv_payload,
            )
            version_id = create_version_for_snapshot(
                conn, project_id, base_version_id, actor.id, snapshot_id
            )
            audit(
                conn,
                actor.id,
                "api_dataset.imported",
                project_id,
                connection_id,
                {"version_id": version_id, "snapshot_id": snapshot_id},
            )
            snapshot = snapshots.get_csv_snapshot(conn, project_id, snapshot_id)
    except ContentConflict as exc:
        raise _error(409, "content_conflict", str(exc)) from exc
    except snapshots.CSVValidationError as exc:
        raise _error(422, "api_response_invalid", str(exc), exc.details) from exc
    return {"snapshot": snapshot, "version_id": version_id}
