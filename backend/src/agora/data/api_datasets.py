"""Saved HTTPS API connections that import into immutable CSV snapshots."""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import http.client
import ipaddress
import io
import json
import os
import re
import socket
import ssl
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from agora.core.db import execute, query_all, query_one, transaction


MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 100
MAX_COLUMN_CHARS = 128
MAX_CELL_CHARS = 32_000
MAX_REQUEST_BODY_BYTES = 64 * 1024
MAX_HEADERS = 50
MAX_HEADER_VALUE_CHARS = 8_192
MAX_PREVIEW_ROWS = 100
HTTP_TIMEOUT_SECONDS = 12
HTTP_TOTAL_SECONDS = 20
DNS_TIMEOUT_SECONDS = 3
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agora-api-dns")

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
_RECORD_PATH_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
_FORBIDDEN_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "expect", "upgrade",
    "proxy-connection", "keep-alive", "te", "trailer",
}
_ENCRYPTED_FIELD = "$fernet"


class ApiDatasetError(Exception):
    """An expected, sanitized API dataset failure."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def validate_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiDatasetError("invalid_api_dataset", "Connection settings must be a JSON object.")
    allowed = {"name", "url", "method", "headers", "body", "records_path", "clear_body"}
    if set(payload) - allowed:
        raise ApiDatasetError("invalid_api_dataset", "Connection settings contain unsupported fields.")

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
        raise ApiDatasetError("invalid_api_dataset", "Name must be 1 to 128 characters.")
    url = payload.get("url")
    validate_url(url)
    method = payload.get("method", "GET")
    if not isinstance(method, str) or method not in {"GET", "POST"}:
        raise ApiDatasetError("invalid_api_dataset", "Method must be GET or POST.")

    raw_headers = payload.get("headers", {})
    if not isinstance(raw_headers, dict) or len(raw_headers) > MAX_HEADERS:
        raise ApiDatasetError("invalid_api_dataset", "Headers must be an object with at most 50 entries.")
    headers: dict[str, str | None] = {}
    folded_names: set[str] = set()
    for key, value in raw_headers.items():
        if not isinstance(key, str) or not _HEADER_NAME.fullmatch(key):
            raise ApiDatasetError("invalid_api_dataset", "Header names must be valid HTTP header names.")
        lower = key.casefold()
        if lower in _FORBIDDEN_HEADERS or lower in folded_names:
            raise ApiDatasetError("invalid_api_dataset", "Header name is restricted or duplicated.")
        folded_names.add(lower)
        if value is not None and (
            not isinstance(value, str)
            or len(value) > MAX_HEADER_VALUE_CHARS
            or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value)
        ):
            raise ApiDatasetError("invalid_api_dataset", "Header values must be at most 8192 characters with no line breaks.")
        headers[key] = value

    body = payload.get("body")
    if method == "GET" and body not in (None, {}):
        raise ApiDatasetError("invalid_api_dataset", "A request body is supported only for POST.")
    if body is not None and not isinstance(body, dict):
        raise ApiDatasetError("invalid_api_dataset", "POST body must be a JSON object.")
    if body is not None:
        try:
            body_bytes = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ApiDatasetError("invalid_api_dataset", "POST body must contain valid JSON values.") from exc
        if len(body_bytes) > MAX_REQUEST_BODY_BYTES:
            raise ApiDatasetError("invalid_api_dataset", "POST body exceeds the 64 KiB limit.")

    records_path = payload.get("records_path")
    if records_path is not None:
        if not isinstance(records_path, str) or len(records_path) > 512:
            raise ApiDatasetError("invalid_api_dataset", "Records path must be at most 512 characters.")
        if records_path and any(not _RECORD_PATH_PART.fullmatch(part) for part in records_path.split(".")):
            raise ApiDatasetError("invalid_api_dataset", "Records path must use dot-separated object keys.")
        records_path = records_path.strip() or None

    clear_body = payload.get("clear_body", False)
    if not isinstance(clear_body, bool):
        raise ApiDatasetError("invalid_api_dataset", "clear_body must be a boolean.")

    return {
        "name": name.strip(), "url": url, "method": method,
        "headers": headers, "body": body if method == "POST" else None,
        "records_path": records_path, "clear_body": clear_body,
    }


def validate_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or value != value.strip():
        raise ApiDatasetError("invalid_api_dataset", "Enter a valid HTTPS API URL.")
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
    except ValueError as exc:
        raise ApiDatasetError("invalid_api_dataset", "Enter a valid HTTPS API URL.") from exc
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not 1 <= port <= 65535
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ApiDatasetError("invalid_api_dataset", "API URLs must use HTTPS and cannot include credentials or fragments.")
    return value


def _fernet() -> Fernet:
    key = os.getenv("API_DATA_ENCRYPTION_KEY", "").strip()
    if not key:
        raise ApiDatasetError(
            "api_encryption_unavailable",
            "API credential encryption is not configured on this server.",
            503,
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ApiDatasetError(
            "api_encryption_unavailable",
            "API credential encryption is not configured correctly on this server.",
            503,
        ) from exc


def _seal_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    token = _fernet().encrypt(payload).decode("ascii")
    return json.dumps({_ENCRYPTED_FIELD: token}, separators=(",", ":"))


def _unseal_json(value: str | Any) -> Any:
    try:
        envelope = json.loads(_text(value))
        token = envelope[_ENCRYPTED_FIELD]
        payload = _fernet().decrypt(token.encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except ApiDatasetError:
        raise
    except (TypeError, KeyError, ValueError, UnicodeDecodeError, UnicodeEncodeError, InvalidToken, json.JSONDecodeError) as exc:
        raise ApiDatasetError("api_encryption_unavailable", "Saved API connection could not be decrypted.", 503) from exc


def _seal_url(url: str) -> str:
    return "fernet:v1:" + _fernet().encrypt(url.encode("utf-8")).decode("ascii")


def _unseal_url(value: str | Any) -> str:
    value = _text(value)
    if not value.startswith("fernet:v1:"):
        raise ApiDatasetError("api_encryption_unavailable", "Saved API connection could not be decrypted.", 503)
    try:
        return _fernet().decrypt(value[len("fernet:v1:"):].encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise ApiDatasetError("api_encryption_unavailable", "Saved API connection could not be decrypted.", 503) from exc


def _restore_blank_query_values(candidate: str, previous: str) -> str:
    """Preserve redacted values for query keys still present in an edited URL."""
    new_parts = urlsplit(candidate)
    old_parts = urlsplit(previous)
    same_target = (
        new_parts.scheme.casefold() == old_parts.scheme.casefold()
        and (new_parts.hostname or "").casefold() == (old_parts.hostname or "").casefold()
        and (new_parts.port or 443) == (old_parts.port or 443)
        and new_parts.path == old_parts.path
    )
    if not same_target:
        return candidate
    old_values: dict[str, list[str]] = {}
    for pair in old_parts.query.split("&") if old_parts.query else ():
        key, separator, value = pair.partition("=")
        old_values.setdefault(key, []).append(value if separator else "")
    output: list[str] = []
    for pair in new_parts.query.split("&") if new_parts.query else ():
        key, separator, value = pair.partition("=")
        if separator and value == "" and old_values.get(key):
            value = old_values[key].pop(0)
            output.append(f"{key}={value}")
        else:
            output.append(pair)
    return new_parts._replace(query="&".join(output)).geturl()


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.query:
        return url
    redacted = "&".join(
        pair.partition("=")[0] + "="
        for pair in parsed.query.split("&")
    )
    return parsed._replace(query=redacted).geturl()


def _text(value: Any) -> str:
    if hasattr(value, "read") and callable(value.read):
        value = value.read()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _timestamp(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _public_connection(row: dict[str, Any]) -> dict[str, Any]:
    headers = _unseal_json(row.get("headers_json") or "{}")
    body_saved = row.get("body_json") is not None
    if not isinstance(headers, dict):
        raise ApiDatasetError("api_connection_unavailable", "Saved API connection is unavailable.", 503)
    public_headers = {name: "" for name in headers}
    public_url = _redact_url(_unseal_url(row["url"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "url": public_url,
        "method": row["method"],
        "headers": public_headers,
        "secret_headers": list(headers),
        "body": None,
        "body_saved": body_saved,
        "records_path": row.get("records_path"),
        "updated_at": _timestamp(row.get("updated_at")),
    }


def list_connections(project_id: str) -> list[dict[str, Any]]:
    with transaction() as conn:
        rows = query_all(
            conn,
            """SELECT id, name, url, method, headers_json, body_json, records_path, updated_at
               FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id
               ORDER BY name, id""",
            {"project_id": project_id},
        )
    return [_public_connection(row) for row in rows]


def create_connection(project_id: str, actor_id: str, payload: Any) -> dict[str, Any]:
    config = validate_config(payload)
    headers = {name: value for name, value in config["headers"].items() if value not in (None, "")}
    headers_json = _seal_json(headers)
    body_json = _seal_json(config["body"]) if config["body"] is not None else None
    url_value = _seal_url(config["url"])
    connection_id = str(uuid.uuid4())
    with transaction() as conn:
        existing = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND LOWER(name) = LOWER(:name)",
            {"project_id": project_id, "name": config["name"]},
        )
        if existing:
            raise ApiDatasetError("api_connection_exists", "A connection with this name already exists.", 409)
        execute(
            conn,
            """INSERT INTO TB_TA_AGORA_API_DATASETS
               (id, project_id, name, url, method, headers_json, body_json, records_path, created_by)
               VALUES (:id, :project_id, :name, :url, :method, :headers_json, :body_json, :records_path, :created_by)""",
            {"id": connection_id, "project_id": project_id, "created_by": actor_id,
             "headers_json": headers_json, "body_json": body_json, "name": config["name"],
             "url": url_value, "method": config["method"], "records_path": config["records_path"]},
        )
        row = query_one(
            conn,
            """SELECT id, name, url, method, headers_json, body_json, records_path, updated_at
               FROM TB_TA_AGORA_API_DATASETS WHERE id = :id AND project_id = :project_id""",
            {"id": connection_id, "project_id": project_id},
        )
    return _public_connection(row or {})


def update_connection(project_id: str, connection_id: str, payload: Any) -> dict[str, Any] | None:
    config = validate_config(payload)
    with transaction() as conn:
        existing = query_one(
            conn,
            """SELECT id, url, headers_json, body_json FROM TB_TA_AGORA_API_DATASETS
               WHERE project_id = :project_id AND id = :id""",
            {"project_id": project_id, "id": connection_id},
        )
        if existing is None:
            return None
        duplicate = query_one(
            conn,
            """SELECT id FROM TB_TA_AGORA_API_DATASETS
               WHERE project_id = :project_id AND LOWER(name) = LOWER(:name) AND id <> :id""",
            {"project_id": project_id, "name": config["name"], "id": connection_id},
        )
        if duplicate:
            raise ApiDatasetError("api_connection_exists", "A connection with this name already exists.", 409)
        previous_headers = _unseal_json(_text(existing["headers_json"]))
        previous_url = _unseal_url(existing["url"])
        same_origin = _same_origin(config["url"], previous_url)
        previous_body_exists = existing["body_json"] is not None
        if not same_origin:
            retained_header_names = {
                old.casefold()
                for name, value in config["headers"].items()
                if value == "" and (old := next((key for key in previous_headers if key.casefold() == name.casefold()), None))
            }
            if retained_header_names:
                raise ApiDatasetError(
                    "api_credentials_required",
                    "Re-enter saved header values before changing this API to a different host, or remove those headers.",
                    422,
                )
            if config["method"] == "POST" and previous_body_exists and config["body"] is None and not config["clear_body"]:
                raise ApiDatasetError(
                    "api_payload_required",
                    "Re-enter the saved POST payload before changing this API to a different host, or clear the payload.",
                    422,
                )
        new_headers: dict[str, str] = {}
        for name, value in config["headers"].items():
            previous_name = next((old for old in previous_headers if old.casefold() == name.casefold()), None)
            if value is None:
                continue
            if value == "" and previous_name is not None:
                new_headers[previous_name] = previous_headers[previous_name]
            elif value != "":
                new_headers[name] = value
        headers_json = _seal_json(new_headers)
        retained_url = _restore_blank_query_values(config["url"], previous_url)
        url_value = _seal_url(retained_url)
        if config["method"] == "GET":
            body_json = None
        elif config["body"] is not None:
            body_json = _seal_json(config["body"])
        elif config["clear_body"]:
            body_json = None
        else:
            body_json = _text(existing["body_json"])
        execute(
            conn,
            """UPDATE TB_TA_AGORA_API_DATASETS SET name = :name, url = :url, method = :method,
                  headers_json = :headers_json, body_json = :body_json, records_path = :records_path,
                  updated_at = SYSTIMESTAMP WHERE project_id = :project_id AND id = :id""",
            {"project_id": project_id, "id": connection_id, "headers_json": headers_json,
             "body_json": body_json, "name": config["name"], "url": url_value,
             "method": config["method"], "records_path": config["records_path"]},
        )
        row = query_one(
            conn,
            """SELECT id, name, url, method, headers_json, body_json, records_path, updated_at
               FROM TB_TA_AGORA_API_DATASETS WHERE id = :id AND project_id = :project_id""",
            {"id": connection_id, "project_id": project_id},
        )
    return _public_connection(row or {})


def delete_connection(project_id: str, connection_id: str) -> bool:
    with transaction() as conn:
        row = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :id",
            {"project_id": project_id, "id": connection_id},
        )
        if row is None:
            return False
        execute(conn, "DELETE FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :id",
                {"project_id": project_id, "id": connection_id})
    return True


def _resolve_public_ip(host: str, port: int) -> str:
    future = _DNS_EXECUTOR.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    try:
        answers = future.result(timeout=DNS_TIMEOUT_SECONDS)
    except FutureTimeout as exc:
        future.cancel()
        raise ApiDatasetError("api_timeout", "The API host lookup timed out.", 504) from exc
    except OSError as exc:
        raise ApiDatasetError("api_unavailable", "The API host could not be reached.", 502) from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise ApiDatasetError("api_unavailable", "The API host could not be reached.", 502)
    try:
        parsed_addresses = [ipaddress.ip_address(address.split("%", 1)[0]) for address in addresses]
    except ValueError as exc:
        raise ApiDatasetError("api_unavailable", "The API host could not be reached.", 502) from exc
    if any(not address.is_global for address in parsed_addresses):
        raise ApiDatasetError("api_destination_blocked", "API hosts must resolve only to public IP addresses.", 422)
    return addresses[0]


def _same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    return (
        a.scheme.casefold() == b.scheme.casefold()
        and (a.hostname or "").casefold() == (b.hostname or "").casefold()
        and (a.port or 443) == (b.port or 443)
    )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a checked public address while retaining the URL host for TLS and Host."""

    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float, deadline: float) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip
        self._deadline = deadline

    def connect(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("API request deadline elapsed")
        sock = socket.create_connection((self._pinned_ip, self.port), min(self.timeout, remaining))
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("API request deadline elapsed")
            self.sock.settimeout(min(HTTP_TIMEOUT_SECONDS, remaining))
        except BaseException:
            sock.close()
            raise


def _request_json(url: str, method: str, headers: dict[str, str], body: dict[str, Any] | None) -> Any:
    validate_url(url)
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or 443
    started = time.monotonic()
    deadline = started + HTTP_TOTAL_SECONDS
    pinned_ip = _resolve_public_ip(host, port)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    encoded_body = None
    request_headers = {"Accept": "application/json", "User-Agent": "Agora-API-Dataset/1.0"}
    for name, value in headers.items():
        if value is None or value == "":
            continue
        prior_name = next((key for key in request_headers if key.casefold() == name.casefold()), None)
        if prior_name is not None:
            del request_headers[prior_name]
        request_headers[name] = value
    if body is not None:
        encoded_body = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if not any(key.casefold() == "content-type" for key in request_headers):
            request_headers["Content-Type"] = "application/json"

    conn = _PinnedHTTPSConnection(
        host,
        port,
        pinned_ip,
        min(HTTP_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic())),
        deadline,
    )
    try:
        conn.request(method, path, body=encoded_body, headers=request_headers)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ApiDatasetError("api_timeout", "The API request timed out.", 504)
        if conn.sock is not None:
            conn.sock.settimeout(min(HTTP_TIMEOUT_SECONDS, remaining))
        response = conn.getresponse()
        if response.status < 200 or response.status >= 300:
            raise ApiDatasetError("api_response_error", "The API returned an unsuccessful response.", 502)
        if response.status in {204, 205}:
            raise ApiDatasetError("api_response_invalid", "The API response did not contain JSON data.", 502)
        chunks: list[bytes] = []
        byte_count = 0
        content_length = response.getheader("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    raise ApiDatasetError("api_response_too_large", "The API response exceeds the 8 MiB limit.", 413)
            except ValueError:
                raise ApiDatasetError("api_response_invalid", "The API response has invalid size metadata.", 502)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiDatasetError("api_timeout", "The API request timed out.", 504)
            if conn.sock is not None:
                conn.sock.settimeout(min(HTTP_TIMEOUT_SECONDS, remaining))
            chunk = response.read1(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - byte_count))
            if not chunk:
                break
            chunks.append(chunk)
            byte_count += len(chunk)
            if byte_count > MAX_RESPONSE_BYTES:
                raise ApiDatasetError("api_response_too_large", "The API response exceeds the 8 MiB limit.", 413)
        payload = b"".join(chunks)
        try:
            return json.loads(payload.decode("utf-8-sig"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiDatasetError("api_response_invalid", "The API response must be valid JSON.", 502) from exc
    except ApiDatasetError:
        raise
    except TimeoutError as exc:
        raise ApiDatasetError("api_timeout", "The API request timed out.", 504) from exc
    except (OSError, ssl.SSLError, http.client.HTTPException, UnicodeEncodeError) as exc:
        raise ApiDatasetError("api_unavailable", "The API request could not be completed.", 502) from exc
    finally:
        conn.close()


def _records_from_response(response: Any, records_path: str | None) -> list[dict[str, Any]]:
    selected = response
    if records_path:
        for part in records_path.split("."):
            if not isinstance(selected, dict) or part not in selected:
                raise ApiDatasetError("api_records_not_found", "Records path was not found in the API response.", 422)
            selected = selected[part]
    elif isinstance(response, dict):
        selected = response.get("data", response)
    if not isinstance(selected, list) or any(not isinstance(row, dict) for row in selected):
        raise ApiDatasetError("api_records_invalid", "The API response must contain an array of objects.", 422)
    if len(selected) > MAX_ROWS:
        raise ApiDatasetError("api_records_too_large", "The API returned more than 100,000 records.", 413)
    return selected


def _csv_for_records(records: list[dict[str, Any]]) -> tuple[bytes, list[str]]:
    columns: list[str] = []
    known: set[str] = set()
    for row in records:
        for key in row:
            if not isinstance(key, str) or not key.strip() or len(key) > MAX_COLUMN_CHARS:
                raise ApiDatasetError("api_records_invalid", "API record fields must have valid names up to 128 characters.", 422)
            folded = key.casefold()
            if folded in known and key not in columns:
                raise ApiDatasetError("api_records_invalid", "API record fields cannot differ only by letter case.", 422)
            if folded not in known:
                known.add(folded)
                columns.append(key)
            if len(columns) > MAX_COLUMNS:
                raise ApiDatasetError("api_records_too_wide", "The API returned more than 100 fields per record.", 413)
    if not columns:
        raise ApiDatasetError("api_records_empty", "The API returned no records with fields.", 422)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row_number, record in enumerate(records, start=1):
        values: dict[str, str] = {}
        for column in columns:
            value = record.get(column)
            if value is None:
                cell = ""
            elif isinstance(value, (str, int, float, bool)):
                cell = str(value)
            else:
                try:
                    cell = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                except (TypeError, ValueError) as exc:
                    raise ApiDatasetError("api_records_invalid", "The API returned an unsupported value.", 422) from exc
            if len(cell) > MAX_CELL_CHARS:
                raise ApiDatasetError("api_records_invalid", f"API record {row_number} contains a cell over 32,000 characters.", 422)
            values[column] = cell
        writer.writerow(values)
    return output.getvalue().encode("utf-8"), columns


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def preview_config(config: dict[str, Any]) -> dict[str, Any]:
    response = _request_json(config["url"], config["method"], config["headers"], config["body"])
    records = _records_from_response(response, config["records_path"])
    csv_bytes, columns = _csv_for_records(records)
    # validate the exact bytes that import would store, including snapshot limits.
    from agora.data.snapshots import CSVValidationError, validate_csv

    try:
        validated = validate_csv("api-preview.csv", csv_bytes)
    except CSVValidationError as exc:
        raise ApiDatasetError("api_response_invalid", str(exc), 422) from exc
    clean_preview = records[:MAX_PREVIEW_ROWS]
    return {
        "columns": columns,
        "rows": clean_preview,
        "row_count": validated.row_count,
        "preview_limit": MAX_PREVIEW_ROWS,
    }


def test_connection(project_id: str, connection_id: str) -> dict[str, Any] | None:
    with transaction() as conn:
        row = query_one(
            conn,
            """SELECT id, name, url, method, headers_json, body_json, records_path
               FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :id""",
            {"project_id": project_id, "id": connection_id},
        )
    if row is None:
        return None
    config = {
        "url": _unseal_url(row["url"]), "method": row["method"],
        "headers": _unseal_json(_text(row["headers_json"])),
        "body": _unseal_json(_text(row["body_json"])) if row["body_json"] is not None else None,
        "records_path": row.get("records_path"),
    }
    return preview_config(config)


def get_config_for_project(project_id: str, connection_id: str) -> dict[str, Any] | None:
    with transaction() as conn:
        row = query_one(
            conn,
            """SELECT id, name, url, method, headers_json, body_json, records_path
               FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :id""",
            {"project_id": project_id, "id": connection_id},
        )
    if row is None:
        return None
    return {
        "id": row["id"], "name": row["name"], "url": _unseal_url(row["url"]), "method": row["method"],
        "headers": _unseal_json(_text(row["headers_json"])),
        "body": _unseal_json(_text(row["body_json"])) if row["body_json"] is not None else None,
        "records_path": row.get("records_path"),
    }


def response_to_csv(config: dict[str, Any]) -> tuple[bytes, list[str], int]:
    response = _request_json(config["url"], config["method"], config["headers"], config["body"])
    records = _records_from_response(response, config["records_path"])
    payload, columns = _csv_for_records(records)
    from agora.data.snapshots import CSVValidationError, validate_csv

    try:
        validated = validate_csv("api-import.csv", payload)
    except CSVValidationError as exc:
        status = 413 if len(payload) > 8 * 1024 * 1024 else 422
        raise ApiDatasetError("api_response_invalid", str(exc), status) from exc
    return payload, columns, validated.row_count
