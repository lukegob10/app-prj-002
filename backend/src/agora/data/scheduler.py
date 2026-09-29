"""Durable scheduling and execution for saved API dataset refreshes."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import date, datetime, time as day_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agora.core.db import execute, query_all, query_one, transaction


MIN_INTERVAL_MINUTES = 15
MAX_INTERVAL_MINUTES = 10_080
MAX_ATTEMPTS = 3
LEASE_SECONDS = 90
RUN_HISTORY_LIMIT = 50
_TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_LOGGER = logging.getLogger(__name__)

_SAFE_ERROR_MESSAGES = {
    "api_timeout": "The API request timed out.",
    "api_unavailable": "The API could not be reached.",
    "api_destination_blocked": "The API host did not resolve to an allowed public address.",
    "api_encryption_unavailable": "Saved API credentials are unavailable; check server encryption configuration.",
    "api_connection_unavailable": "The saved API connection is unavailable.",
    "api_response_error": "The API returned an unsuccessful response.",
    "api_response_invalid": "The API response could not be imported as tabular data.",
    "api_response_too_large": "The API response exceeded the supported size limit.",
    "api_records_not_found": "The configured records path was not found in the API response.",
    "api_records_invalid": "The API response did not contain valid tabular records.",
    "api_records_empty": "The API response did not contain records with fields.",
    "api_records_too_large": "The API returned more rows than Agora supports.",
    "api_records_too_wide": "The API returned more columns than Agora supports.",
    "worker_lease_expired": "The refresh worker stopped before completing the run.",
    "internal_error": "The refresh could not be completed. Try again or contact an administrator.",
}
_RETRYABLE_GET_ERRORS = {"api_timeout", "api_unavailable"}


class ScheduleError(Exception):
    """A sanitized schedule validation or state error."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _schedule_error(message: str) -> ScheduleError:
    return ScheduleError("invalid_api_schedule", message)


def validate_schedule(payload: Any) -> dict[str, Any]:
    """Validate and normalize the public schedule shape."""
    allowed = {
        "enabled", "frequency", "interval_minutes", "local_time", "weekdays", "day_of_month",
        "timezone", "base_version_id", "publish_mode",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise _schedule_error("Schedule settings must be a JSON object with supported fields.")
    required = {"enabled", "frequency", "timezone", "base_version_id", "publish_mode"}
    if not required.issubset(payload):
        raise _schedule_error("Provide enabled, frequency, timezone, base_version_id, and publish_mode.")

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise _schedule_error("enabled must be a boolean.")

    frequency = payload.get("frequency")
    if not isinstance(frequency, str) or frequency not in {"interval", "daily", "weekly", "monthly"}:
        raise _schedule_error("frequency must be interval, daily, weekly, or monthly.")

    interval = payload.get("interval_minutes")
    local_time = payload.get("local_time")
    weekdays = payload.get("weekdays")
    day_of_month = payload.get("day_of_month")
    if frequency == "interval":
        if isinstance(interval, bool) or not isinstance(interval, int) or not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
            raise _schedule_error("interval_minutes must be between 15 and 10080 minutes.")
        if local_time is not None or weekdays not in (None, []) or day_of_month is not None:
            raise _schedule_error("interval schedules do not use local_time, weekdays, or day_of_month.")
        local_time = None
        weekdays = []
    elif frequency == "monthly":
        if interval is not None:
            raise _schedule_error("monthly schedules do not use interval_minutes.")
        if not isinstance(local_time, str) or not _TIME_PATTERN.fullmatch(local_time):
            raise _schedule_error("local_time must use 24-hour HH:MM format.")
        if isinstance(day_of_month, bool) or not isinstance(day_of_month, int) or not 1 <= day_of_month <= 28:
            raise _schedule_error("day_of_month must be between 1 and 28.")
        if weekdays not in (None, []):
            raise _schedule_error("monthly schedules do not use weekdays.")
        weekdays = []
    else:
        if interval is not None:
            raise _schedule_error("daily and weekly schedules do not use interval_minutes.")
        if not isinstance(local_time, str) or not _TIME_PATTERN.fullmatch(local_time):
            raise _schedule_error("local_time must use 24-hour HH:MM format.")
        if day_of_month is not None:
            raise _schedule_error("daily and weekly schedules do not use day_of_month.")
        if frequency == "daily":
            if weekdays not in (None, []):
                raise _schedule_error("daily schedules do not use weekdays.")
            weekdays = []
        else:
            if not isinstance(weekdays, list) or not weekdays:
                raise _schedule_error("weekly schedules need at least one weekday.")
            if any(isinstance(day, bool) or not isinstance(day, int) or day < 1 or day > 7 for day in weekdays):
                raise _schedule_error("weekdays must contain ISO weekday numbers from 1 to 7.")
            if len(set(weekdays)) != len(weekdays):
                raise _schedule_error("weekdays cannot contain duplicates.")
            weekdays = sorted(weekdays)

    timezone_name = payload.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name or len(timezone_name) > 64:
        raise _schedule_error("timezone must be an IANA timezone name.")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise _schedule_error("timezone must be a supported IANA timezone name.") from exc

    base_version_id = payload.get("base_version_id")
    if not isinstance(base_version_id, str) or not base_version_id.strip() or len(base_version_id) > 36:
        raise _schedule_error("Choose a content version for the scheduled data refresh.")
    publish_mode = payload.get("publish_mode")
    if not isinstance(publish_mode, str) or publish_mode not in {"draft", "auto_publish"}:
        raise _schedule_error("publish_mode must be draft or auto_publish.")

    return {
        "enabled": enabled,
        "frequency": frequency,
        "interval_minutes": interval,
        "local_time": local_time,
        "weekdays": weekdays,
        "day_of_month": day_of_month,
        "timezone": timezone_name,
        "base_version_id": base_version_id.strip(),
        "publish_mode": publish_mode,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wall_occurrence(local_date: date, local_time: day_time, zone: ZoneInfo) -> datetime | None:
    """Return the first fold of a valid wall time; a DST gap has no occurrence."""
    naive = datetime.combine(local_date, local_time)
    candidates: list[datetime] = []
    for fold in (0, 1):
        localized = naive.replace(tzinfo=zone, fold=fold)
        utc_candidate = localized.astimezone(timezone.utc)
        if utc_candidate.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.append(utc_candidate)
    # In a fall-back fold, both offsets round-trip. The first occurrence is the
    # earlier UTC instant; the second fold is deliberately never scheduled.
    return min(candidates) if candidates else None


def next_occurrence(config: dict[str, Any], after: datetime) -> datetime:
    """Return the next intended run slot strictly after an aware datetime.

    Interval schedules are elapsed-time based. Daily and weekly schedules use
    local wall time; a nonexistent spring-forward slot is skipped, and an
    ambiguous fall-back slot runs once at its first occurrence.
    """
    after_utc = _as_utc(after)
    frequency = config["frequency"]
    if frequency == "interval":
        return after_utc + timedelta(minutes=int(config["interval_minutes"]))

    zone = ZoneInfo(config["timezone"])
    hour, minute = (int(part) for part in config["local_time"].split(":"))
    target_time = day_time(hour, minute)
    local_start = after_utc.astimezone(zone).date()
    selected_weekdays = set(config.get("weekdays") or [])
    if frequency == "monthly":
        for month_offset in range(14):
            month_index = local_start.year * 12 + local_start.month - 1 + month_offset
            year, month_zero = divmod(month_index, 12)
            candidate_date = date(year, month_zero + 1, int(config["day_of_month"]))
            candidate = _wall_occurrence(candidate_date, target_time, zone)
            if candidate is not None and candidate > after_utc:
                return candidate
        raise ValueError("Could not find the next recurrence slot.")
    # Nine days are enough to find a valid daily/weekly slot even when today's
    # local time is in a DST gap.
    for offset in range(9):
        candidate_date = local_start + timedelta(days=offset)
        if frequency == "weekly" and candidate_date.isoweekday() not in selected_weekdays:
            continue
        candidate = _wall_occurrence(candidate_date, target_time, zone)
        if candidate is not None and candidate > after_utc:
            return candidate
    raise ValueError("Could not find the next recurrence slot.")


def _next_after_missed_slot(config: dict[str, Any], due_at: datetime, now: datetime) -> datetime:
    due_utc = _as_utc(due_at)
    now_utc = _as_utc(now)
    if config["frequency"] == "interval":
        interval = timedelta(minutes=int(config["interval_minutes"]))
        elapsed = max(0, int((now_utc - due_utc).total_seconds() // interval.total_seconds()))
        return due_utc + interval * (elapsed + 1)
    candidate = next_occurrence(config, due_utc)
    while candidate <= now_utc:
        candidate = next_occurrence(config, candidate)
    return candidate


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _json_value(value: Any) -> Any:
    if hasattr(value, "read") and callable(value.read):
        value = value.read()
    if isinstance(value, str):
        return json.loads(value)
    return value


def _schedule_shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "connection_id": row["connection_id"],
        "enabled": bool(row["enabled"]),
        "frequency": row["frequency"],
        "interval_minutes": int(row["interval_minutes"]) if row.get("interval_minutes") is not None else None,
        "local_time": row.get("local_time"),
        "weekdays": _json_value(row.get("weekdays_json") or "[]"),
        "day_of_month": int(row["day_of_month"]) if row.get("day_of_month") is not None else None,
        "timezone": row["timezone"],
        "base_version_id": row["base_version_id"],
        "publish_mode": row["publish_mode"],
        "next_run_at": _iso(row.get("next_run_at")),
        "last_run_at": _iso(row.get("last_run_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def get_schedule(project_id: str, connection_id: str) -> dict[str, Any] | None:
    with transaction() as conn:
        row = query_one(
            conn,
            """SELECT connection_id, enabled, frequency, interval_minutes, local_time,
                      weekdays_json, day_of_month, timezone, base_version_id, publish_mode,
                      next_run_at, last_run_at, created_at, updated_at
               FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id""",
            {"project_id": project_id, "connection_id": connection_id},
        )
    return _schedule_shape(row) if row else None


def connection_exists(project_id: str, connection_id: str) -> bool:
    with transaction() as conn:
        row = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :connection_id",
            {"project_id": project_id, "connection_id": connection_id},
        )
    return row is not None


def save_schedule(
    project_id: str, connection_id: str, actor_id: str, payload: Any, role: str = "editor"
) -> dict[str, Any] | None:
    config = validate_schedule(payload)
    now = datetime.now(timezone.utc)
    next_run_at = next_occurrence(config, now) if config["enabled"] else None
    with transaction() as conn:
        connection_row = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :connection_id",
            {"project_id": project_id, "connection_id": connection_id},
        )
        if connection_row is None:
            return None
        base = query_one(
            conn,
            "SELECT id FROM TB_TA_AGORA_CONTENT_VERSIONS WHERE project_id = :project_id AND id = :version_id",
            {"project_id": project_id, "version_id": config["base_version_id"]},
        )
        if base is None:
            raise ScheduleError("version_not_found", "The selected content version was not found in this project.", 404)
        existing = query_one(
            conn,
            """SELECT connection_id, enabled, frequency, interval_minutes, local_time,
                      weekdays_json, day_of_month, timezone, base_version_id, publish_mode,
                      publish_approved_by
               FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id FOR UPDATE""",
            {"project_id": project_id, "connection_id": connection_id},
        )
        if config["publish_mode"] == "auto_publish" and role not in {"owner", "admin"}:
            existing_weekdays = _json_value(existing.get("weekdays_json") or "[]") if existing else []
            unchanged_fields = bool(existing) and all(
                config[field] == (
                    existing_weekdays if field == "weekdays" else existing.get(field)
                )
                for field in (
                    "frequency", "interval_minutes", "local_time", "weekdays", "day_of_month",
                    "timezone", "base_version_id", "publish_mode",
                )
            )
            enabled_same_or_paused = bool(existing) and (
                config["enabled"] == bool(existing["enabled"])
                or (bool(existing["enabled"]) and not config["enabled"])
            )
            if not (existing and existing["publish_mode"] == "auto_publish" and unchanged_fields and enabled_same_or_paused):
                raise ScheduleError(
                    "auto_publish_owner_required",
                    "Only a project owner or admin can enable or change automatic publishing.",
                    403,
                )
        publish_approver = (
            (existing.get("publish_approved_by") if existing else None)
            if role not in {"owner", "admin"} and config["publish_mode"] == "auto_publish"
            else actor_id if config["publish_mode"] == "auto_publish" else None
        )
        weekdays_json = json.dumps(config["weekdays"], separators=(",", ":"))
        params = {
            "project_id": project_id, "connection_id": connection_id,
            "enabled": 1 if config["enabled"] else 0, "frequency": config["frequency"],
            "interval_minutes": config["interval_minutes"], "local_time": config["local_time"],
            "weekdays_json": weekdays_json, "timezone": config["timezone"],
            "day_of_month": config["day_of_month"],
            "base_version_id": config["base_version_id"], "publish_mode": config["publish_mode"],
            "publish_approved_by": publish_approver, "next_run_at": next_run_at,
            "actor_id": actor_id,
        }
        if existing is None:
            execute(
                conn,
                """INSERT INTO TB_TA_AGORA_API_DATASET_SCHEDULES
                   (connection_id, project_id, enabled, frequency, interval_minutes, local_time,
                    weekdays_json, day_of_month, timezone, base_version_id, publish_mode, publish_approved_by,
                    next_run_at, created_by)
                   VALUES (:connection_id, :project_id, :enabled, :frequency, :interval_minutes,
                    :local_time, :weekdays_json, :day_of_month, :timezone, :base_version_id, :publish_mode,
                    :publish_approved_by, :next_run_at, :actor_id)""",
                params,
            )
        else:
            # A saved queued occurrence belongs to the old schedule definition.
            # Cancel it when settings change; manually queued runs are retained.
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'cancelled', finished_at = SYSTIMESTAMP,
                       error_code = 'schedule_changed',
                       error_message = 'Schedule settings changed before the run started.'
                   WHERE project_id = :project_id AND connection_id = :connection_id
                     AND status = 'queued' AND is_manual = 0""",
                {"project_id": project_id, "connection_id": connection_id},
            )
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES
                   SET enabled = :enabled, frequency = :frequency, interval_minutes = :interval_minutes,
                       local_time = :local_time, weekdays_json = :weekdays_json, day_of_month = :day_of_month,
                       timezone = :timezone,
                       base_version_id = :base_version_id, publish_mode = :publish_mode,
                       publish_approved_by = :publish_approved_by, next_run_at = :next_run_at,
                       updated_at = SYSTIMESTAMP
                   WHERE project_id = :project_id AND connection_id = :connection_id""",
                {key: value for key, value in params.items() if key != "actor_id"},
            )
        row = query_one(
            conn,
            """SELECT connection_id, enabled, frequency, interval_minutes, local_time,
                      weekdays_json, day_of_month, timezone, base_version_id, publish_mode,
                      next_run_at, last_run_at, created_at, updated_at
               FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id""",
            {"project_id": project_id, "connection_id": connection_id},
        )
    return _schedule_shape(row or {})


def _run_shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "status": row["status"],
        "is_manual": bool(row["is_manual"]),
        "scheduled_for": _iso(row.get("scheduled_for")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "attempt_count": int(row.get("attempt_count") or 0),
        "version_id": row.get("version_id"),
        "snapshot_id": row.get("snapshot_id"),
        "published": bool(row["published"]) if row.get("published") is not None else None,
        "unchanged": bool(row.get("unchanged", 0)),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "created_at": _iso(row.get("created_at")),
    }


def list_runs(project_id: str, connection_id: str, limit: int = 20) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= RUN_HISTORY_LIMIT:
        raise ScheduleError("invalid_limit", "Run history limit must be between 1 and 50.")
    with transaction() as conn:
        rows = query_all(
            conn,
            """SELECT id, status, is_manual, scheduled_for, started_at, finished_at,
                      attempt_count, version_id, snapshot_id, published, unchanged,
                      error_code, error_message, created_at
               FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE project_id = :project_id AND connection_id = :connection_id
               ORDER BY created_at DESC, id DESC FETCH FIRST :limit ROWS ONLY""",
            {"project_id": project_id, "connection_id": connection_id, "limit": limit},
        )
    return [_run_shape(row) for row in rows]


def queue_manual_run(
    project_id: str, connection_id: str, actor_id: str, role: str = "editor"
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with transaction() as conn:
        schedule = query_one(
            conn,
            """SELECT connection_id, enabled, base_version_id, publish_mode, publish_approved_by, created_by
               FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id FOR UPDATE""",
            {"project_id": project_id, "connection_id": connection_id},
        )
        if schedule is None:
            raise ScheduleError("api_schedule_not_found", "Create a schedule before running this connection.", 404)
        if schedule["publish_mode"] == "auto_publish" and role not in {"owner", "admin"}:
            raise ScheduleError(
                "auto_publish_owner_required",
                "Only a project owner or admin can run an automatic publish now.",
                403,
            )
        if schedule["publish_mode"] == "auto_publish" and not bool(schedule["enabled"]):
            raise ScheduleError(
                "auto_publish_schedule_disabled",
                "Enable the automatic publishing schedule before running it now.",
                409,
            )
        active = query_one(
            conn,
            """SELECT COUNT(*) AS run_count FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE project_id = :project_id AND connection_id = :connection_id
                 AND status IN ('queued', 'running')""",
            {"project_id": project_id, "connection_id": connection_id},
        )
        if int((active or {}).get("run_count") or 0) > 0:
            raise ScheduleError("api_schedule_run_active", "A refresh for this connection is already queued or running.", 409)
        run_id = str(uuid.uuid4())
        publish_mode = schedule["publish_mode"]
        publish_actor = schedule.get("publish_approved_by") if publish_mode == "auto_publish" else None
        execute(
            conn,
            """INSERT INTO TB_TA_AGORA_API_DATASET_RUNS
               (id, connection_id, project_id, status, is_manual, scheduled_for, available_at,
                base_version_id, publish_mode, version_actor_id, publish_actor_id, requested_by)
               VALUES (:id, :connection_id, :project_id, 'queued', 1, :scheduled_for, :available_at,
                :base_version_id, :publish_mode, :version_actor_id, :publish_actor_id, :requested_by)""",
            {"id": run_id, "connection_id": connection_id, "project_id": project_id,
             "scheduled_for": now, "available_at": now, "base_version_id": schedule["base_version_id"],
             "publish_mode": publish_mode, "version_actor_id": actor_id,
             "publish_actor_id": publish_actor, "requested_by": actor_id},
        )
        row = query_one(
            conn,
            """SELECT id, status, is_manual, scheduled_for, started_at, finished_at,
                      attempt_count, version_id, snapshot_id, published, unchanged,
                      error_code, error_message, created_at
               FROM TB_TA_AGORA_API_DATASET_RUNS WHERE id = :id""",
            {"id": run_id},
        )
    return _run_shape(row or {})


def _next_run_slot(config: dict[str, Any], due_at: datetime, now: datetime) -> datetime:
    return _next_after_missed_slot(config, due_at, now)


def enqueue_due_runs(limit: int = 25) -> int:
    """Create one run for each due schedule and advance over missed slots."""
    inserted = 0
    now = datetime.now(timezone.utc)
    with transaction() as conn:
        for _ in range(max(1, min(limit, 100))):
            row = query_one(
                conn,
                """SELECT connection_id, project_id, frequency, interval_minutes, local_time,
                          weekdays_json, day_of_month, timezone, base_version_id, publish_mode,
                          publish_approved_by, created_by, next_run_at
                   FROM TB_TA_AGORA_API_DATASET_SCHEDULES
                   WHERE enabled = 1 AND next_run_at <= SYSTIMESTAMP AND ROWNUM = 1
                   FOR UPDATE SKIP LOCKED""",
                {},
            )
            if row is None:
                break
            weekdays = _json_value(row.get("weekdays_json") or "[]")
            config = {
                "frequency": row["frequency"], "interval_minutes": row.get("interval_minutes"),
                "local_time": row.get("local_time"), "weekdays": weekdays,
                "day_of_month": row.get("day_of_month"),
                "timezone": row["timezone"],
            }
            slot = _as_utc(row["next_run_at"])
            following = _next_run_slot(config, slot, now)
            publish_mode = row["publish_mode"]
            approved = row.get("publish_approved_by") if publish_mode == "auto_publish" else None
            execute(
                conn,
                """INSERT INTO TB_TA_AGORA_API_DATASET_RUNS
                   (id, connection_id, project_id, status, is_manual, scheduled_for, available_at,
                    base_version_id, publish_mode, version_actor_id, publish_actor_id)
                   VALUES (:id, :connection_id, :project_id, 'queued', 0, :scheduled_for, SYSTIMESTAMP,
                    :base_version_id, :publish_mode, :version_actor_id, :publish_actor_id)""",
                {"id": str(uuid.uuid4()), "connection_id": row["connection_id"],
                 "project_id": row["project_id"], "scheduled_for": slot,
                 "base_version_id": row["base_version_id"], "publish_mode": publish_mode,
                 "version_actor_id": approved or row["created_by"], "publish_actor_id": approved},
            )
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES
                   SET next_run_at = :next_run_at, updated_at = SYSTIMESTAMP
                   WHERE project_id = :project_id AND connection_id = :connection_id""",
                {"next_run_at": following, "project_id": row["project_id"], "connection_id": row["connection_id"]},
            )
            inserted += 1
    return inserted


def _reap_expired_claim() -> bool:
    with transaction() as conn:
        candidate = query_one(
            conn,
            """SELECT id, project_id, connection_id FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE status = 'running' AND lease_until <= SYSTIMESTAMP AND ROWNUM = 1""",
            {},
        )
        if candidate is None:
            return False
        # Take locks in the same schedule -> run order used by claim and
        # schedule edits. This also matches the connection -> schedule -> run
        # cascade order used when deleting a saved API connection.
        schedule = query_one(
            conn,
            """SELECT connection_id FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id
               FOR UPDATE""",
            {"project_id": candidate["project_id"], "connection_id": candidate["connection_id"]},
        )
        if schedule is None:
            return False
        row = query_one(
            conn,
            """SELECT id, connection_id, project_id, attempt_count
               FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE id = :id AND status = 'running' AND lease_until <= SYSTIMESTAMP
               FOR UPDATE""",
            {"id": candidate["id"]},
        )
        if row is None:
            return False
        connection_row = query_one(
            conn,
            "SELECT method FROM TB_TA_AGORA_API_DATASETS WHERE project_id = :project_id AND id = :id",
            {"project_id": row["project_id"], "id": row["connection_id"]},
        )
        retryable = connection_row is not None and connection_row["method"] == "GET"
        attempts = int(row.get("attempt_count") or 0)
        if retryable and attempts < MAX_ATTEMPTS:
            delay = min(60, 10 * (2 ** max(0, attempts - 1)))
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'queued', available_at = SYSTIMESTAMP + NUMTODSINTERVAL(:delay, 'SECOND'),
                       claim_token = NULL, lease_until = NULL, error_code = 'worker_lease_expired',
                       error_message = :message
                   WHERE id = :id AND status = 'running'""",
                {"delay": delay, "id": row["id"], "message": _SAFE_ERROR_MESSAGES["worker_lease_expired"]},
            )
        else:
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'failed', finished_at = SYSTIMESTAMP,
                       claim_token = NULL, lease_until = NULL, error_code = 'worker_lease_expired',
                       error_message = :message
                   WHERE id = :id AND status = 'running'""",
                {"id": row["id"], "message": _SAFE_ERROR_MESSAGES["worker_lease_expired"]},
            )
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP
                   WHERE project_id = :project_id AND connection_id = :connection_id""",
                {"project_id": row["project_id"], "connection_id": row["connection_id"]},
            )
        return True


def claim_next_run() -> dict[str, Any] | None:
    """Claim one queue item with a DB lease and a per-connection exclusion lock."""
    _reap_expired_claim()
    with transaction() as conn:
        candidates = query_all(
            conn,
            """SELECT q.id, q.connection_id, q.project_id, q.attempt_count, q.base_version_id,
                      q.publish_mode, q.version_actor_id, q.publish_actor_id, q.requested_by, q.is_manual
               FROM TB_TA_AGORA_API_DATASET_RUNS q
               WHERE q.status = 'queued' AND q.available_at <= SYSTIMESTAMP AND ROWNUM <= 100
                 AND NOT EXISTS (
                     SELECT 1 FROM TB_TA_AGORA_API_DATASET_RUNS active
                     WHERE active.project_id = q.project_id AND active.connection_id = q.connection_id
                       AND active.status = 'running'
                 )""",
            {},
        )
        seen_connections: set[str] = set()
        for row in candidates:
            connection_id = row["connection_id"]
            if connection_id in seen_connections:
                continue
            seen_connections.add(connection_id)
            # Locking the schedule row serializes claims for the same connection.
            # The run update below is a conditional state transition, so an editor
            # pausing the schedule or another claimant cannot steal the job.
            schedule = query_one(
                conn,
                """SELECT enabled FROM TB_TA_AGORA_API_DATASET_SCHEDULES
                   WHERE project_id = :project_id AND connection_id = :connection_id FOR UPDATE""",
                {"project_id": row["project_id"], "connection_id": connection_id},
            )
            if schedule is None:
                continue
            if not bool(schedule["enabled"]) and not bool(row["is_manual"]):
                execute(
                    conn,
                    """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                       SET status = 'cancelled', finished_at = SYSTIMESTAMP,
                           error_code = 'schedule_paused',
                           error_message = 'The schedule was paused before the run started.'
                       WHERE id = :id AND status = 'queued'""",
                    {"id": row["id"]},
                )
                continue
            active = query_one(
                conn,
                """SELECT COUNT(*) AS run_count FROM TB_TA_AGORA_API_DATASET_RUNS
                   WHERE project_id = :project_id AND connection_id = :connection_id AND status = 'running'""",
                {"project_id": row["project_id"], "connection_id": connection_id},
            )
            if int((active or {}).get("run_count") or 0) > 0:
                continue
            project = query_one(
                conn,
                "SELECT published_version_id FROM TB_TA_AGORA_PROJECTS WHERE id = :project_id",
                {"project_id": row["project_id"]},
            )
            if project is None:
                continue
            dataset = query_one(
                conn,
                """SELECT updated_at FROM TB_TA_AGORA_API_DATASETS
                   WHERE project_id = :project_id AND id = :connection_id""",
                {"project_id": row["project_id"], "connection_id": connection_id},
            )
            if dataset is None:
                continue
            expected_published = project.get("published_version_id")
            expected_publication_at = _latest_publication_at(conn, row["project_id"])
            source_version = (
                expected_published or row["base_version_id"]
                if row["publish_mode"] == "auto_publish"
                else row["base_version_id"]
            )
            token = str(uuid.uuid4())
            lease_until = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)
            changed = execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'running', attempt_count = attempt_count + 1,
                       claim_token = :claim_token, lease_until = :lease_until,
                       started_at = COALESCE(started_at, SYSTIMESTAMP),
                       source_version_id = :source_version_id,
                       expected_published_id = :expected_published_id,
                       expected_publication_at = :expected_publication_at,
                       connection_updated_at = :connection_updated_at
                   WHERE id = :id AND status = 'queued'""",
                {"claim_token": token, "lease_until": lease_until, "source_version_id": source_version,
                 "expected_published_id": expected_published,
                 "expected_publication_at": expected_publication_at,
                 "connection_updated_at": dataset["updated_at"], "id": row["id"]},
            )
            if changed != 1:
                continue
            row.update({
                "claim_token": token, "source_version_id": source_version,
                "expected_published_id": expected_published,
                "expected_publication_at": expected_publication_at,
                "connection_updated_at": dataset["updated_at"],
                "attempt_count": int(row.get("attempt_count") or 0) + 1,
            })
            return row
        return None


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    from agora.data.api_datasets import ApiDatasetError

    if isinstance(exc, ApiDatasetError):
        code = exc.code if _CODE_PATTERN.fullmatch(exc.code) else "api_refresh_failed"
        message = _SAFE_ERROR_MESSAGES.get(code, "The API refresh could not be completed.")
        return code, message, code in _RETRYABLE_GET_ERRORS
    return "internal_error", _SAFE_ERROR_MESSAGES["internal_error"], False


def _record_failure(claim: dict[str, Any], code: str, message: str, retryable: bool) -> None:
    retryable = retryable and claim.get("method") == "GET"
    with transaction() as conn:
        schedule = query_one(
            conn,
            """SELECT connection_id FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id
               FOR UPDATE""",
            {"project_id": claim["project_id"], "connection_id": claim["connection_id"]},
        )
        if schedule is None:
            return
        current = query_one(
            conn,
            """SELECT attempt_count FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE id = :id AND status = 'running' AND claim_token = :claim_token
                 AND lease_until > SYSTIMESTAMP FOR UPDATE""",
            {"id": claim["id"], "claim_token": claim["claim_token"]},
        )
        if current is None:
            return
        attempts = int(current.get("attempt_count") or 0)
        if retryable and attempts < MAX_ATTEMPTS:
            delay = min(60, 10 * (2 ** max(0, attempts - 1)))
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'queued', available_at = SYSTIMESTAMP + NUMTODSINTERVAL(:delay, 'SECOND'),
                       claim_token = NULL, lease_until = NULL, error_code = :error_code,
                       error_message = :error_message
                   WHERE id = :id AND claim_token = :claim_token""",
                {"delay": delay, "error_code": code, "error_message": message,
                 "id": claim["id"], "claim_token": claim["claim_token"]},
            )
        else:
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'failed', finished_at = SYSTIMESTAMP,
                       claim_token = NULL, lease_until = NULL, error_code = :error_code,
                       error_message = :error_message
                   WHERE id = :id AND claim_token = :claim_token""",
                {"error_code": code, "error_message": message,
                 "id": claim["id"], "claim_token": claim["claim_token"]},
            )
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP
                   WHERE project_id = :project_id AND connection_id = :connection_id""",
                {"project_id": claim["project_id"], "connection_id": claim["connection_id"]},
            )


def _snapshot_digest(conn: Any, project_id: str, version_id: str | None) -> tuple[str | None, str | None]:
    if not version_id:
        return None, None
    row = query_one(
        conn,
        """SELECT s.id AS snapshot_id, s.sha256 AS sha256
           FROM TB_TA_AGORA_VERSION_CSV_BINDINGS b
           JOIN TB_TA_AGORA_CSV_SNAPSHOTS s ON s.project_id = b.project_id AND s.id = b.snapshot_id
           WHERE b.project_id = :project_id AND b.version_id = :version_id""",
        {"project_id": project_id, "version_id": version_id},
    )
    return (row["snapshot_id"], row["sha256"]) if row else (None, None)


def _version_package_id(conn: Any, project_id: str, version_id: str | None) -> str | None:
    if not version_id:
        return None
    row = query_one(
        conn,
        "SELECT package_id FROM TB_TA_AGORA_CONTENT_VERSIONS WHERE project_id = :project_id AND id = :version_id",
        {"project_id": project_id, "version_id": version_id},
    )
    return row.get("package_id") if row else None


def _latest_publication_at(conn: Any, project_id: str) -> Any:
    row = query_one(
        conn,
        "SELECT MAX(created_at) AS publication_at FROM TB_TA_AGORA_CONTENT_PUBLICATIONS WHERE project_id = :project_id",
        {"project_id": project_id},
    )
    return row.get("publication_at") if row else None


def _latest_snapshot_run(conn: Any, project_id: str, connection_id: str) -> dict[str, Any] | None:
    return query_one(
        conn,
        """SELECT version_id, snapshot_id, published, status
           FROM TB_TA_AGORA_API_DATASET_RUNS
           WHERE project_id = :project_id AND connection_id = :connection_id
             AND status IN ('succeeded', 'unchanged', 'needs_review') AND snapshot_id IS NOT NULL
           ORDER BY created_at DESC, id DESC FETCH FIRST 1 ROWS ONLY""",
        {"project_id": project_id, "connection_id": connection_id},
    )


def _has_publish_authority(
    conn: Any, project_id: str, actor_id: str | None, owner_id: str | None = None
) -> bool:
    if not actor_id:
        return False
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT is_admin FROM TB_TA_AGORA_USERS WHERE id = :actor_id FOR UPDATE",
            {"actor_id": actor_id},
        )
        user = cursor.fetchone()
    if user is None:
        return False
    if bool(user[0]) or owner_id == actor_id:
        return True
    membership = query_one(
        conn,
        """SELECT role FROM TB_TA_AGORA_MEMBERSHIPS
           WHERE project_id = :project_id AND account_id = :actor_id FOR UPDATE""",
        {"project_id": project_id, "actor_id": actor_id},
    )
    return membership is not None and membership.get("role") == "owner"


def _finish_success(claim: dict[str, Any], csv_payload: bytes, connection_name: str) -> None:
    from agora.content.repository import ContentConflict, create_version_for_snapshot, publish
    from agora.core.projects import audit
    from agora.data import snapshots

    filename_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", connection_name.strip()).strip(".-_")[:80] or "dataset"
    filename = f"api-{filename_slug}-{claim['connection_id'][:8]}.csv"
    validated = snapshots.validate_csv(filename, csv_payload)
    payload_hash = validated.sha256
    review_code: str | None = None

    with transaction() as conn:
        # Follow the ownership and deletion order: project -> connection ->
        # schedule -> run. This also serializes publication with human changes.
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT published_version_id, owner_id FROM TB_TA_AGORA_PROJECTS WHERE id = :project_id FOR UPDATE",
                {"project_id": claim["project_id"]},
            )
            project = cursor.fetchone()
        if project is None:
            return
        dataset_state = query_one(
            conn,
            """SELECT updated_at FROM TB_TA_AGORA_API_DATASETS
               WHERE project_id = :project_id AND id = :connection_id FOR UPDATE""",
            {"project_id": claim["project_id"], "connection_id": claim["connection_id"]},
        )
        if dataset_state is None:
            return
        current_schedule = query_one(
            conn,
            """SELECT enabled, publish_mode, publish_approved_by
               FROM TB_TA_AGORA_API_DATASET_SCHEDULES
               WHERE project_id = :project_id AND connection_id = :connection_id FOR UPDATE""",
            {"project_id": claim["project_id"], "connection_id": claim["connection_id"]},
        )
        if current_schedule is None:
            return
        current_run = query_one(
            conn,
            """SELECT id FROM TB_TA_AGORA_API_DATASET_RUNS
               WHERE id = :id AND status = 'running' AND claim_token = :claim_token
                 AND lease_until > SYSTIMESTAMP FOR UPDATE""",
            {"id": claim["id"], "claim_token": claim["claim_token"]},
        )
        if current_run is None:
            return

        current_published = project[0]
        current_publication_at = _latest_publication_at(conn, claim["project_id"])
        current_snapshot_id, current_digest = _snapshot_digest(conn, claim["project_id"], current_published)
        latest = _latest_snapshot_run(conn, claim["project_id"], claim["connection_id"])
        latest_snapshot_id = latest.get("snapshot_id") if latest else None
        latest_digest = None
        if latest_snapshot_id:
            latest_snapshot = query_one(
                conn,
                "SELECT sha256 FROM TB_TA_AGORA_CSV_SNAPSHOTS WHERE project_id = :project_id AND id = :snapshot_id",
                {"project_id": claim["project_id"], "snapshot_id": latest_snapshot_id},
            )
            latest_digest = latest_snapshot.get("sha256") if latest_snapshot else None

        # If the live version already has these exact CSV bytes, there is no
        # dashboard change to publish and no new immutable objects to create.
        if claim["publish_mode"] == "auto_publish" and current_published and current_digest == payload_hash:
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'unchanged', finished_at = SYSTIMESTAMP, claim_token = NULL,
                       lease_until = NULL, version_id = :version_id, snapshot_id = :snapshot_id,
                       published = 1, unchanged = 1, error_code = NULL, error_message = NULL
                   WHERE id = :id AND claim_token = :claim_token""",
                {"version_id": current_published, "snapshot_id": current_snapshot_id,
                 "id": claim["id"], "claim_token": claim["claim_token"]},
            )
            execute(conn, "UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP WHERE connection_id = :id",
                    {"id": claim["connection_id"]})
            return

        latest_matches_base = False
        if claim["publish_mode"] == "draft" and latest:
            base_package_id = _version_package_id(conn, claim["project_id"], claim["base_version_id"])
            latest_package_id = _version_package_id(conn, claim["project_id"], latest.get("version_id"))
            latest_matches_base = bool(base_package_id) and latest_package_id == base_package_id
        if claim["publish_mode"] == "draft" and latest and latest_digest == payload_hash and latest_matches_base:
            execute(
                conn,
                """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                   SET status = 'unchanged', finished_at = SYSTIMESTAMP, claim_token = NULL,
                       lease_until = NULL, version_id = :version_id, snapshot_id = :snapshot_id,
                       published = CASE WHEN :is_published = 1 THEN 1 ELSE 0 END,
                       unchanged = 1, error_code = NULL, error_message = NULL
                   WHERE id = :id AND claim_token = :claim_token""",
                {"version_id": latest["version_id"], "snapshot_id": latest_snapshot_id,
                 "is_published": 1 if latest.get("version_id") == current_published else 0,
                 "id": claim["id"], "claim_token": claim["claim_token"]},
            )
            execute(conn, "UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP WHERE connection_id = :id",
                    {"id": claim["connection_id"]})
            return

        if claim["publish_mode"] == "draft":
            base_snapshot_id, base_digest = _snapshot_digest(conn, claim["project_id"], claim["base_version_id"])
            if base_snapshot_id and base_digest == payload_hash:
                execute(
                    conn,
                    """UPDATE TB_TA_AGORA_API_DATASET_RUNS
                       SET status = 'unchanged', finished_at = SYSTIMESTAMP, claim_token = NULL,
                           lease_until = NULL, version_id = :version_id, snapshot_id = :snapshot_id,
                           published = CASE WHEN :is_published = 1 THEN 1 ELSE 0 END,
                           unchanged = 1, error_code = NULL, error_message = NULL
                       WHERE id = :id AND claim_token = :claim_token""",
                    {"version_id": claim["base_version_id"], "snapshot_id": base_snapshot_id,
                     "is_published": 1 if claim["base_version_id"] == current_published else 0,
                     "id": claim["id"], "claim_token": claim["claim_token"]},
                )
                execute(conn, "UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP WHERE connection_id = :id",
                        {"id": claim["connection_id"]})
                return

        expected_published = claim.get("expected_published_id")
        publication_event_changed = (
            "expected_publication_at" in claim
            and current_publication_at != claim.get("expected_publication_at")
        )
        pointer_changed = current_published != expected_published or publication_event_changed
        approval_valid = True
        connection_config_changed = False
        if claim["publish_mode"] == "auto_publish":
            approval_valid = bool(
                current_schedule
                and current_schedule["publish_mode"] == "auto_publish"
                and current_schedule.get("publish_approved_by") == claim.get("publish_actor_id")
                and bool(current_schedule["enabled"])
            ) and _has_publish_authority(
                conn, claim["project_id"], claim.get("publish_actor_id"), project[1]
            )
            connection_config_changed = (
                dataset_state is None
                or ("connection_updated_at" in claim
                    and dataset_state.get("updated_at") != claim.get("connection_updated_at"))
            )
        if claim["publish_mode"] == "auto_publish" and pointer_changed:
            review_code = "publication_changed"
        elif claim["publish_mode"] == "auto_publish" and not approval_valid:
            review_code = "publish_approval_changed"
        elif claim["publish_mode"] == "auto_publish" and connection_config_changed:
            review_code = "api_connection_changed"

        # Reuse the prior immutable snapshot when bytes match. Otherwise store
        # the newly validated response exactly once in this transaction.
        snapshot_id = latest_snapshot_id if latest_digest == payload_hash else None
        if snapshot_id is None:
            snapshot_id = snapshots.create_csv_snapshot(
                conn, claim["project_id"], claim["version_actor_id"], filename, csv_payload
            )
        source_version_id = claim.get("source_version_id") or claim["base_version_id"]
        if review_code and current_published:
            # Preserve the fetched data for review while retaining the HTML
            # package a human most recently published.
            source_version_id = current_published
        version_id = create_version_for_snapshot(
            conn, claim["project_id"], source_version_id,
            claim["version_actor_id"], snapshot_id,
        )
        published = False
        if claim["publish_mode"] == "auto_publish" and review_code is None:
            try:
                publish(conn, claim["project_id"], claim["publish_actor_id"], version_id)
            except ContentConflict as exc:
                # A second publication guard protects against any unexpected
                # change between pointer comparison and publish.
                raise ContentConflict("The published dashboard changed during this refresh.") from exc
            published = True

        audit_actor = claim.get("publish_actor_id") if published else claim["version_actor_id"]
        audit(
            conn, audit_actor, "api_dataset.scheduled_imported", claim["project_id"],
            claim["connection_id"],
            {"run_id": claim["id"], "version_id": version_id, "snapshot_id": snapshot_id,
             "published": published, "needs_review": review_code is not None},
        )
        status = "needs_review" if review_code else "succeeded"
        message = (
            "Data was imported into a working version. Review and publish it because the published dashboard changed during the refresh."
            if review_code == "publication_changed" else
            "Data was imported into a working version. Review and publish it because the owner approval changed during the refresh."
            if review_code == "publish_approval_changed" else
            "Connection settings changed during refresh; review the working version and run again."
            if review_code == "api_connection_changed" else None
        )
        execute(
            conn,
            """UPDATE TB_TA_AGORA_API_DATASET_RUNS
               SET status = :status, finished_at = SYSTIMESTAMP, claim_token = NULL,
                   lease_until = NULL, version_id = :version_id, snapshot_id = :snapshot_id,
                   published = :published, unchanged = 0,
                   error_code = :error_code, error_message = :error_message
               WHERE id = :id AND status = 'running' AND claim_token = :claim_token""",
            {"status": status, "version_id": version_id, "snapshot_id": snapshot_id,
             "published": 1 if published else 0, "error_code": review_code,
             "error_message": message, "id": claim["id"], "claim_token": claim["claim_token"]},
        )
        execute(
            conn,
            "UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES SET last_run_at = SYSTIMESTAMP WHERE connection_id = :id",
            {"id": claim["connection_id"]},
        )


def process_claim(claim: dict[str, Any]) -> bool:
    """Fetch and persist a claimed refresh; stale lease holders cannot commit."""
    from agora.data import api_datasets

    try:
        config = api_datasets.get_config_for_project(claim["project_id"], claim["connection_id"])
        if config is None:
            raise api_datasets.ApiDatasetError("api_connection_unavailable", "The saved API connection is unavailable.", 404)
        claim["method"] = config["method"]
        csv_payload, _, _ = api_datasets.response_to_csv(config)
        _finish_success(claim, csv_payload, config["name"])
        return True
    except Exception as exc:
        code, message, retryable = _safe_failure(exc)
        # Do not log exception strings: transport libraries can include secrets
        # or request targets in them.
        _LOGGER.error("Scheduled API dataset run %s failed (%s).", claim.get("id"), code)
        _record_failure(claim, code, message, retryable)
        return False


def worker_once() -> bool:
    """Enqueue due schedules and process at most one run."""
    enqueue_due_runs()
    claim = claim_next_run()
    if claim is None:
        return False
    process_claim(claim)
    return True


def run_worker(stop_event: threading.Event | None = None, poll_seconds: float = 2.0) -> None:
    """Poll Oracle until stopped. Multiple worker processes are safe to run."""
    stopped = stop_event or threading.Event()
    while not stopped.is_set():
        try:
            worked = worker_once()
        except Exception as exc:
            _LOGGER.error("Scheduler worker iteration failed (%s).", type(exc).__name__)
            worked = False
        if not worked:
            stopped.wait(max(0.25, poll_seconds))


def main() -> None:
    import signal

    logging.basicConfig(level=os.getenv("AGORA_SCHEDULER_LOG_LEVEL", "INFO").upper())
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    poll_seconds = float(os.getenv("AGORA_SCHEDULER_POLL_SECONDS", "2"))
    run_worker(stop, poll_seconds)


if __name__ == "__main__":
    main()
