"""Permission-checked CSV, saved-record, and live-source API routes."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from agora.core.auth import Actor, require_actor, require_csrf
from agora.core.db import StorageUnavailable, connection, transaction
from agora.core.projects import audit, get_project, require_project_role
from agora.data import records, snapshots, sources


router = APIRouter(prefix="/api", tags=["data"])


def _error(status_code: int, code: str, message: str, details: Any = None) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)


def _record_error(exc: records.RecordServiceError) -> HTTPException:
    return _error(exc.status_code, exc.code, exc.message, exc.details)


def _source_error(exc: sources.SourceError) -> HTTPException:
    return _error(exc.status_code, exc.code, str(exc))


def _source_call(operation: Any, *args: Any) -> Any:
    try:
        return operation(*args)
    except StorageUnavailable:
        raise
    except sources.SourceError as exc:
        raise _source_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise _error(502, "source_unavailable", "The approved source could not complete this request.") from exc
    except Exception as exc:
        raise _error(502, "source_unavailable", "The approved source could not complete this request.") from exc


@router.post("/projects/{project_id}/csv")
async def upload_csv(
    project_id: str,
    file: UploadFile = File(...),
    base_version_id: str | None = Form(None),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    payload = await file.read(snapshots.MAX_CSV_BYTES + 1)
    try:
        snapshots.validate_csv(file.filename or "", payload)
    except snapshots.CSVValidationError as exc:
        status = 413 if len(payload) > snapshots.MAX_CSV_BYTES else 422
        raise _error(status, "invalid_csv", str(exc), exc.details) from exc

    from agora.content.repository import ContentConflict, create_version_for_snapshot, list_versions

    try:
        with transaction() as conn:
            versions = list_versions(conn, project_id)
            chosen_id = base_version_id or (versions[0]["id"] if versions else None)
            if chosen_id is None:
                raise _error(409, "html_version_required", "Upload an HTML package before replacing its CSV.")
            snapshot_id = snapshots.create_csv_snapshot(
                conn, project_id, actor.id, file.filename or "", payload
            )
            version_id = create_version_for_snapshot(
                conn, project_id, chosen_id, actor.id, snapshot_id
            )
            audit(
                conn, actor.id, "csv.snapshot.created", project_id, snapshot_id,
                {"version_id": version_id, "base_version_id": chosen_id},
            )
            snapshot = snapshots.get_csv_snapshot(conn, project_id, snapshot_id)
    except ContentConflict as exc:
        raise _error(409, "content_conflict", str(exc)) from exc
    return {"snapshot": snapshot, "version_id": version_id}


@router.get("/projects/{project_id}/csv/snapshots")
def list_snapshots(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "editor")
    with connection() as conn:
        result = snapshots.list_csv_snapshots(conn, project_id)
    return {"snapshots": result}


@router.get("/projects/{project_id}/csv/snapshots/{snapshot_id}/preview")
def preview_snapshot(
    project_id: str, snapshot_id: str, actor: Actor = Depends(require_actor),
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = snapshots.DEFAULT_PREVIEW_PAGE_SIZE,
) -> dict[str, Any]:
    require_project_role(project_id, actor, "editor")
    with connection() as conn:
        result = snapshots.preview_csv_snapshot(conn, project_id, snapshot_id, page, page_size)
    if result is None:
        raise _error(404, "csv_not_found", "CSV snapshot was not found.")
    return result


@router.get("/projects/{project_id}/csv/snapshots/{snapshot_id}/download")
def download_snapshot(
    project_id: str, snapshot_id: str, actor: Actor = Depends(require_actor)
) -> Response:
    require_project_role(project_id, actor, "editor")
    with connection() as conn:
        metadata = snapshots.get_csv_snapshot(conn, project_id, snapshot_id)
        payload = snapshots.read_csv_snapshot(conn, project_id, snapshot_id)
    if metadata is None or payload is None:
        raise _error(404, "csv_not_found", "CSV snapshot was not found.")
    filename = metadata["filename"]
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=\"snapshot.csv\"; filename*=UTF-8''"
            + quote(filename, safe="")
        },
    )


@router.get("/projects/{project_id}/versions/{version_id}/csv")
def version_csv(
    project_id: str, version_id: str, actor: Actor = Depends(require_actor)
) -> Response:
    role = require_project_role(project_id, actor, "viewer")
    with connection() as conn:
        project = get_project(conn, project_id)
        if role == "viewer" and project["published_version_id"] != version_id:
            raise _error(404, "version_not_found", "Version was not found.")
        from agora.content.repository import get_version
        if get_version(conn, project_id, version_id) is None:
            raise _error(404, "version_not_found", "Version was not found.")
        snapshot_id = snapshots.get_bound_snapshot_id(conn, version_id)
        if snapshot_id is None:
            raise _error(404, "csv_not_found", "This version has no CSV snapshot.")
        payload = snapshots.read_csv_snapshot(conn, project_id, snapshot_id)
    if payload is None:
        raise _error(404, "csv_not_found", "CSV snapshot was not found.")
    return Response(content=payload, media_type="text/csv; charset=utf-8")


@router.get("/projects/{project_id}/records")
def list_records(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    try:
        return {"records": records.list_records(project_id, actor)}
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc


@router.get("/projects/{project_id}/records/export")
def export_records(
    project_id: str,
    format: str = Query("json"),
    actor: Actor = Depends(require_actor),
) -> Response:
    try:
        result = records.export_records(project_id, actor, format)
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc
    return Response(
        content=result.body,
        media_type=result.content_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get("/projects/{project_id}/records/{record_id}")
def get_record(
    project_id: str, record_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, dict[str, Any]]:
    try:
        return {"record": records.get_record(project_id, record_id, actor)}
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc


@router.post("/projects/{project_id}/records")
def create_record(
    project_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, dict[str, Any]]:
    try:
        return {"record": records.create_record(project_id, actor, payload.get("data"))}
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc


@router.put("/projects/{project_id}/records/{record_id}")
def update_record(
    project_id: str,
    record_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, dict[str, Any]]:
    try:
        return {
            "record": records.update_record(
                project_id, record_id, actor, payload.get("data"), payload.get("expected_revision")
            )
        }
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc


@router.delete("/projects/{project_id}/records/{record_id}")
def delete_record(
    project_id: str,
    record_id: str,
    expected_revision: int | None = Query(None),
    payload: dict[str, Any] | None = Body(None),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    try:
        revision = expected_revision if expected_revision is not None else (payload or {}).get("expected_revision")
        return records.delete_record(
            project_id, record_id, actor, revision
        )
    except records.RecordServiceError as exc:
        raise _record_error(exc) from exc


@router.get("/admin/data-sources")
def admin_sources(actor: Actor = Depends(require_actor)) -> dict[str, list[dict[str, Any]]]:
    if not actor.is_admin:
        raise _error(403, "admin_required", "Platform administrator access is required.")
    return {"sources": _source_call(sources.list_available_sources)}


@router.post("/admin/data-sources")
def admin_create_source(
    payload: dict[str, Any] = Body(...), actor: Actor = Depends(require_csrf)
) -> dict[str, dict[str, Any]]:
    if not actor.is_admin:
        raise _error(403, "admin_required", "Platform administrator access is required.")
    return {"source": _source_call(sources.create_source, payload, actor.id)}


@router.get("/projects/{project_id}/sources")
def project_sources(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "viewer")
    return {"sources": _source_call(sources.list_project_sources, project_id)}


@router.get("/projects/{project_id}/available-sources")
def available_project_sources(
    project_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[dict[str, Any]]]:
    require_project_role(project_id, actor, "owner")
    available = _source_call(sources.list_available_sources)
    return {"sources": [source for source in available if source["enabled"]]}


@router.put("/projects/{project_id}/sources/{source_id}")
def grant_source(
    project_id: str, source_id: str, actor: Actor = Depends(require_csrf)
) -> dict[str, bool]:
    require_project_role(project_id, actor, "owner")
    _source_call(sources.grant_source, project_id, source_id, actor.id)
    return {"granted": True}


@router.delete("/projects/{project_id}/sources/{source_id}")
def revoke_source(
    project_id: str, source_id: str, actor: Actor = Depends(require_csrf)
) -> dict[str, bool]:
    require_project_role(project_id, actor, "owner")
    _source_call(sources.revoke_source, project_id, source_id, actor.id)
    return {"revoked": True}


@router.get("/projects/{project_id}/sources/{source_id}/catalogs")
def source_catalogs(
    project_id: str, source_id: str, actor: Actor = Depends(require_actor)
) -> dict[str, list[str]]:
    require_project_role(project_id, actor, "viewer")
    return {"catalogs": _source_call(sources.browse_source, project_id, source_id, "catalogs")}


@router.get("/projects/{project_id}/sources/{source_id}/schemas")
def source_schemas(
    project_id: str,
    source_id: str,
    catalog: str = Query(...),
    actor: Actor = Depends(require_actor),
) -> dict[str, list[str]]:
    require_project_role(project_id, actor, "viewer")
    return {"schemas": _source_call(sources.browse_source, project_id, source_id, "schemas", catalog)}


@router.get("/projects/{project_id}/sources/{source_id}/tables")
def source_tables(
    project_id: str,
    source_id: str,
    catalog: str = Query(...),
    schema: str = Query(...),
    actor: Actor = Depends(require_actor),
) -> dict[str, list[str]]:
    require_project_role(project_id, actor, "viewer")
    return {"tables": _source_call(sources.browse_source, project_id, source_id, "tables", catalog, schema)}


@router.post("/projects/{project_id}/sources/{source_id}/rows")
def source_rows(
    project_id: str,
    source_id: str,
    payload: dict[str, Any] = Body(...),
    actor: Actor = Depends(require_csrf),
) -> dict[str, Any]:
    require_project_role(project_id, actor, "viewer")
    return _source_call(sources.read_source_rows, project_id, source_id, payload)
