"""ASGI composition for the Agora API."""

import os
import logging
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

load_dotenv()

from agora.core import auth, projects
from agora.core.db import StorageUnavailable, connection, query_all
from agora.core.schema import missing_schema_objects
from agora.content.router import router as content_router
from agora.data.api_router import router as api_data_router
from agora.data.router import router as data_router


app = FastAPI(title="Agora", version="0.1.0", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)


@app.middleware("http")
async def isolate_api_resources(request: Request, call_next):
    """Keep sandboxed project documents out of session-authorized API routes."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/content/grants/"):
        site = request.headers.get("sec-fetch-site")
        destination = request.headers.get("sec-fetch-dest", "").lower()
        is_view_frame = destination == "iframe" and path.endswith("/view")
        allowed_destination = destination in {"", "empty", "document"} or is_view_frame
        if site not in {None, "same-origin", "none"} or not allowed_destination:
            return JSONResponse(
                {"error": {"code": "resource_isolation", "message": "This request is not allowed from embedded content."}},
                status_code=403,
            )
    response = await call_next(request)
    if path.startswith("/api/") and not path.startswith("/api/content/grants/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Vary"] = "Sec-Fetch-Site, Sec-Fetch-Dest"
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        payload = detail
    elif isinstance(detail, dict) and "code" in detail and "message" in detail:
        payload = {"error": detail}
    else:
        payload = {"error": {"code": f"http_{exc.status_code}", "message": str(detail)}}
    return JSONResponse(payload, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        {"error": {"code": "validation_error", "message": "Check the submitted fields.", "details": details}},
        status_code=422,
    )


@app.exception_handler(StorageUnavailable)
async def storage_error(_: Request, exc: StorageUnavailable) -> JSONResponse:
    logger.warning("Oracle connection unavailable: %s", type(exc.__cause__).__name__)
    return JSONResponse(
        {"error": {"code": "storage_unavailable", "message": "Agora storage is unavailable. Try again shortly."}},
        status_code=503,
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled Agora request error")
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "The request could not be completed."}},
        status_code=500,
    )


@app.get("/api/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/ready")
def ready() -> JSONResponse:
    if os.getenv("ENV", "DEV").upper() == "PROD":
        try:
            public_origin = urlsplit(os.getenv("AGORA_PUBLIC_ORIGIN", ""))
            hostname = public_origin.hostname or ""
            local_http_origin = (
                public_origin.scheme == "http"
                and hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
            )
            allow_insecure_http = os.getenv("AGORA_ALLOW_INSECURE_HTTP", "").strip().lower() in {"1", "true", "yes"}
            valid_origin = (
                (public_origin.scheme == "https" or local_http_origin or (allow_insecure_http and public_origin.scheme == "http"))
                and bool(public_origin.hostname)
                and public_origin.username is None
                and public_origin.password is None
                and public_origin.path in {"", "/"}
                and not public_origin.query
                and not public_origin.fragment
            )
            _ = public_origin.port
        except ValueError:
            valid_origin = False
        if not valid_origin:
            return JSONResponse(
                {
                    "status": "not_ready",
                    "reason": "A valid HTTPS AGORA_PUBLIC_ORIGIN is required in production; localhost HTTP is allowed for local runs.",
                },
                status_code=503,
            )
    try:
        with connection() as conn:
            missing = missing_schema_objects(conn)
            if any(missing.values()):
                return JSONResponse(
                    {
                        "status": "not_ready",
                        "reason": "Oracle schema is incomplete; install the baseline for a new database or run required migrations for an existing database.",
                    },
                    status_code=503,
                )
    except StorageUnavailable:
        return JSONResponse(
            {"status": "not_ready", "reason": "Oracle or the application schema is unavailable."},
            status_code=503,
        )
    except Exception as exc:
        error = exc.args[0] if exc.args else None
        reason = "Oracle schema tables are missing." if getattr(error, "code", None) == 942 else "Oracle readiness query failed."
        return JSONResponse({"status": "not_ready", "reason": reason}, status_code=503)
    return JSONResponse({"status": "ok"})


app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(content_router)
app.include_router(data_router)
app.include_router(api_data_router)
