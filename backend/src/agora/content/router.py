"""Upload, preview, publish, and safely serve immutable HTML packages."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from agora.content.bundle import BundleValidationError, parse_bundle
from agora.content.repository import (
    ContentConflict, create_package_version, create_view_grant, get_asset,
    get_version, get_view_grant, latest_publication, list_versions, publish, published_version, rollback,
)
from agora.core.auth import Actor, require_actor, require_csrf
from agora.core.db import connection, query_one, transaction
from agora.core.projects import audit, require_project_role


router = APIRouter(tags=["content"])


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _read_limit(upload: UploadFile, limit: int) -> bytes:
    payload = upload.file.read(limit + 1)
    if len(payload) > limit:
        raise _error(413, "upload_too_large", f"File exceeds the {limit // (1024 * 1024)} MiB limit.")
    return payload


def _can_read_version(project_id: str, version_id: str, actor: Actor) -> dict:
    role = require_project_role(project_id, actor, "viewer")
    with connection() as conn:
        version = get_version(conn, project_id, version_id)
    if version is None:
        raise _error(404, "version_not_found", "Version was not found.")
    if role not in {"owner", "editor", "admin"} and not version["is_published"]:
        raise _error(403, "draft_forbidden", "Only editors and owners can preview working versions.")
    return version


def _with_snapshot(conn: object, version: dict) -> dict:
    from agora.data.snapshots import get_bound_snapshot_id
    return {**version, "csv_snapshot_id": get_bound_snapshot_id(conn, version["id"])}


@router.post("/api/projects/{project_id}/versions", status_code=201)
def upload_version(project_id: str, package: UploadFile = File(...), csv: UploadFile | None = File(None),
                   actor: Actor = Depends(require_csrf)) -> dict:
    require_project_role(project_id, actor, "editor")
    from agora.content.bundle import MAX_UPLOAD_BYTES
    package_bytes = _read_limit(package, MAX_UPLOAD_BYTES)
    try:
        bundle = parse_bundle(package.filename or "", package_bytes)
    except BundleValidationError as exc:
        raise _error(exc.http_status if exc.http_status != 400 else 422,
                     exc.code, exc.message) from exc
    csv_bytes = _read_limit(csv, 8 * 1024 * 1024) if csv else None
    try:
        with transaction() as conn:
            version_id = create_package_version(conn, project_id, actor.id, bundle)
            snapshot_id = None
            if csv is not None:
                from agora.data.snapshots import bind_csv_snapshot, create_csv_snapshot
                snapshot_id = create_csv_snapshot(conn, project_id, actor.id, csv.filename or "", csv_bytes or b"")
                bind_csv_snapshot(conn, version_id, snapshot_id)
            audit(conn, actor.id, "content.version.create", project_id, version_id,
                  {"package_sha256": get_version(conn, project_id, version_id)["package_sha256"],
                   "csv_snapshot_id": snapshot_id})
            version = get_version(conn, project_id, version_id)
            return _with_snapshot(conn, version)
    except Exception as exc:
        from agora.data.snapshots import CSVValidationError
        if isinstance(exc, CSVValidationError):
            raise _error(422, "csv_invalid", str(exc)) from exc
        raise


@router.get("/api/projects/{project_id}/versions")
def versions(project_id: str, actor: Actor = Depends(require_actor)) -> dict:
    require_project_role(project_id, actor, "editor")
    with connection() as conn:
        return {"versions": [_with_snapshot(conn, version) for version in list_versions(conn, project_id)]}


@router.get("/api/projects/{project_id}/versions/{version_id}")
def version_detail(project_id: str, version_id: str, actor: Actor = Depends(require_actor)) -> dict:
    version = _can_read_version(project_id, version_id, actor)
    with connection() as conn:
        return _with_snapshot(conn, version)


@router.get("/api/projects/{project_id}/published")
def published(project_id: str, actor: Actor = Depends(require_actor)) -> dict:
    require_project_role(project_id, actor, "viewer")
    with connection() as conn:
        version = published_version(conn, project_id)
        return {"version": _with_snapshot(conn, version) if version else None,
                "publication": latest_publication(conn, project_id)}


@router.post("/api/projects/{project_id}/versions/{version_id}/publish")
def publish_version(project_id: str, version_id: str, actor: Actor = Depends(require_csrf)) -> dict:
    require_project_role(project_id, actor, "owner")
    try:
        with transaction() as conn:
            result = publish(conn, project_id, actor.id, version_id)
            audit(conn, actor.id, "content.publish", project_id, version_id,
                  {"previous_version_id": result["previous_version_id"]})
            return result
    except ContentConflict as exc:
        raise _error(409, "publication_conflict", str(exc)) from exc


@router.post("/api/projects/{project_id}/publish/rollback")
def rollback_publication(project_id: str, actor: Actor = Depends(require_csrf)) -> dict:
    require_project_role(project_id, actor, "owner")
    try:
        with transaction() as conn:
            result = rollback(conn, project_id, actor.id)
            audit(conn, actor.id, "content.rollback", project_id, result["published_version_id"],
                  {"previous_version_id": result["previous_version_id"]})
            return result
    except ContentConflict as exc:
        raise _error(409, "rollback_unavailable", str(exc)) from exc


def _make_grant(request: Request, project_id: str, version_id: str, actor: Actor) -> dict:
    version = _can_read_version(project_id, version_id, actor)
    session = request.cookies.get("agora_session")
    if not session:
        raise _error(401, "session_required", "Sign in to view this project.")
    with transaction() as conn:
        token = create_view_grant(conn, project_id, version_id, session)
    entry = quote(version["entry_path"], safe="/")
    return {"url": f"/api/content/grants/{token}/files/{entry}", "grant_token": token,
            "version_id": version_id, "expires_in_seconds": 3600}


@router.post("/api/projects/{project_id}/versions/{version_id}/view-grant")
def view_grant(request: Request, project_id: str, version_id: str,
               actor: Actor = Depends(require_csrf)) -> JSONResponse:
    return JSONResponse(_make_grant(request, project_id, version_id, actor),
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/api/projects/{project_id}/versions/{version_id}/view")
def view_redirect(request: Request, project_id: str, version_id: str,
                  actor: Actor = Depends(require_actor)) -> RedirectResponse:
    grant = _make_grant(request, project_id, version_id, actor)
    return RedirectResponse(grant["url"], status_code=302,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _grant_access(conn: object, token: str) -> dict:
    grant = get_view_grant(conn, token)
    if grant is None:
        raise _error(403, "view_grant_expired", "This view expired. Reload the project to continue.")
    row = query_one(conn, """SELECT p.owner_id, p.published_version_id, a.is_admin, m.role
        FROM TB_TA_AGORA_PROJECTS p JOIN TB_TA_AGORA_USERS a ON a.id = :account_id
        LEFT JOIN TB_TA_AGORA_MEMBERSHIPS m ON m.project_id = p.id AND m.account_id = :account_id
        WHERE p.id = :project_id""",
        {"account_id": grant["account_id"], "project_id": grant["project_id"]})
    if row is None or (row["owner_id"] != grant["account_id"] and not row["is_admin"] and not row["role"]):
        raise _error(403, "project_forbidden", "Project access was revoked.")
    elevated = bool(row["is_admin"]) or row["owner_id"] == grant["account_id"] or row["role"] == "editor"
    if not elevated and row["published_version_id"] != grant["version_id"]:
        raise _error(403, "draft_forbidden", "This version is no longer published.")
    return grant


def _safe_asset_path(path: str) -> str:
    if not path or "\\" in path or "\x00" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise _error(404, "asset_not_found", "Asset was not found.")
    return path


def _asset_csp() -> str:
    return ("sandbox allow-scripts; default-src 'none'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; media-src 'self' blob:; "
            "connect-src 'none'; worker-src 'none'; frame-src 'none'; "
            "object-src 'none'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'self'; navigate-to 'self'")


@router.get("/api/content/grants/{token}/files/{path:path}")
def serve_asset(request: Request, token: str, path: str) -> Response:
    path = _safe_asset_path(path)
    with connection() as conn:
        grant = _grant_access(conn, token)
        asset = get_asset(conn, grant["project_id"], grant["version_id"], path)
    if asset is None:
        raise _error(404, "asset_not_found", "Asset was not found in this version.")
    etag = f'"{asset["sha256"]}"'
    headers = {
        "Cache-Control": "private, no-cache, must-revalidate",
        "ETag": etag,
        "Content-Security-Policy": _asset_csp(),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        "X-Robots-Tag": "noindex, nofollow",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=asset["content"], media_type=asset["mime_type"], headers=headers)


@router.get("/__agora/sdk.js")
def sdk_script() -> Response:
    configured = os.getenv("AGORA_VIEWER_SDK_PATH")
    if configured:
        sdk_path = Path(configured)
    else:
        candidates = [parent / "viewer-sdk" / "sdk.js"
                      for root in (Path.cwd(), Path(__file__).resolve()) for parent in (root, *root.parents)]
        sdk_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if sdk_path is None or not sdk_path.is_file():
        raise _error(503, "sdk_unavailable", "The viewer SDK is not installed on this server.")
    return Response(content=sdk_path.read_bytes(), media_type="text/javascript",
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=300"})
