"""Offline scheduler contract checks; no Oracle or external API access."""

from __future__ import annotations

import unittest
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi import HTTPException

from agora.core import auth
from agora.core.auth import Actor
from agora.data import scheduler
from agora.data import api_router
from agora.main import app


BASE_VERSION_ID = "00000000-0000-4000-8000-000000000001"


class ScheduleRecurrenceTests(unittest.TestCase):
    def _schedule(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "enabled": True,
            "frequency": "daily",
            "interval_minutes": None,
            "local_time": "09:15",
            "timezone": "America/New_York",
            "weekdays": [],
            "day_of_month": None,
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "draft",
        }
        payload.update(overrides)
        return scheduler.validate_schedule(payload)

    def test_interval_occurrence_is_elapsed_time_in_utc(self) -> None:
        schedule = self._schedule(
            frequency="interval",
            interval_minutes=30,
            local_time=None,
            weekdays=[],
            day_of_month=None,
        )
        after = datetime(2026, 9, 24, 14, 5, tzinfo=timezone.utc)

        next_run = scheduler.next_occurrence(schedule, after)

        self.assertEqual(next_run, datetime(2026, 9, 24, 14, 35, tzinfo=timezone.utc))
        self.assertEqual(next_run.utcoffset(), timezone.utc.utcoffset(next_run))

    def test_daily_schedule_skips_nonexistent_local_time_during_spring_gap(self) -> None:
        schedule = self._schedule(local_time="02:30")
        # 2026-03-08 jumps from 01:59 to 03:00 in America/New_York.
        after = datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)

        next_run = scheduler.next_occurrence(schedule, after)

        self.assertEqual(next_run, datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc))

    def test_daily_schedule_uses_only_first_occurrence_during_fall_fold(self) -> None:
        schedule = self._schedule(local_time="01:30")
        # The first 01:30 on 2026-11-01 is 05:30 UTC (fold=0).
        before_first = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
        after_first = datetime(2026, 11, 1, 5, 45, tzinfo=timezone.utc)

        first_run = scheduler.next_occurrence(schedule, before_first)
        next_day_run = scheduler.next_occurrence(schedule, after_first)

        self.assertEqual(first_run, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
        self.assertEqual(next_day_run, datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc))

    def test_weekly_schedule_uses_iso_weekdays_in_configured_timezone(self) -> None:
        schedule = self._schedule(
            frequency="weekly",
            local_time="09:15",
            weekdays=[1, 5],
            day_of_month=None,
        )
        after = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)

        next_run = scheduler.next_occurrence(schedule, after)

        # Thursday afternoon local time advances to Friday 09:15 EDT.
        self.assertEqual(next_run, datetime(2026, 9, 25, 13, 15, tzinfo=timezone.utc))

    def test_monthly_schedule_handles_february_day_28(self) -> None:
        schedule = self._schedule(
            frequency="monthly",
            local_time="09:15",
            weekdays=[],
            day_of_month=28,
        )
        after = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)

        next_run = scheduler.next_occurrence(schedule, after)

        self.assertEqual(next_run, datetime(2026, 2, 28, 14, 15, tzinfo=timezone.utc))

    def test_monthly_schedule_skips_spring_dst_gap_for_that_month(self) -> None:
        schedule = self._schedule(
            frequency="monthly",
            local_time="02:30",
            weekdays=[],
            day_of_month=8,
        )
        # The March 8, 2026 02:30 local slot does not exist in New York.
        after = datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)

        next_run = scheduler.next_occurrence(schedule, after)

        self.assertEqual(next_run, datetime(2026, 4, 8, 6, 30, tzinfo=timezone.utc))


class SchedulerWorkerSafetyTests(unittest.TestCase):
    payload = b"id,value\r\n1,ok\r\n"
    connection_updated_at = datetime(2026, 9, 27, 11, tzinfo=timezone.utc)

    def _claim(self, **overrides: object) -> dict[str, object]:
        claim: dict[str, object] = {
            "id": "run-1",
            "claim_token": "lease-1",
            "project_id": "project-1",
            "connection_id": "connection-1",
            "publish_mode": "auto_publish",
            "source_version_id": "old-version",
            "base_version_id": "old-version",
            "expected_published_id": "old-version",
            "expected_publication_at": None,
            "connection_updated_at": self.connection_updated_at,
            "publish_actor_id": "owner-1",
            "version_actor_id": "owner-1",
        }
        claim.update(overrides)
        return claim

    def _auto_publish_query(
        self, updated_at: datetime, *, enabled: int = 1,
        publish_mode: str = "auto_publish", approver: str = "owner-1",
        run_missing: bool = False,
        lock_order: list[str] | None = None,
    ):
        run_row = None if run_missing else {"id": "run-1"}

        def query_one(_connection: object, sql: str, _params: object) -> dict[str, object] | None:
            if "FROM TB_TA_AGORA_API_DATASETS" in sql:
                if lock_order is not None:
                    self.assertIn("FOR UPDATE", sql)
                    lock_order.append("dataset")
                return {"updated_at": updated_at}
            if "FROM TB_TA_AGORA_API_DATASET_SCHEDULES" in sql:
                if lock_order is not None:
                    self.assertIn("FOR UPDATE", sql)
                    lock_order.append("schedule")
                return {"enabled": enabled, "publish_mode": publish_mode, "publish_approved_by": approver}
            if "FROM TB_TA_AGORA_API_DATASET_RUNS" in sql:
                if lock_order is not None:
                    self.assertIn("FOR UPDATE", sql)
                    lock_order.append("run")
                return run_row
            self.fail(f"Unexpected query in auto-publish finish: {sql}")

        return query_one

    def _project_connection(
        self, published_version_id: str | None, lock_order: list[str] | None = None,
    ) -> tuple[Mock, Mock]:
        cursor = Mock()
        if lock_order is not None:
            def record_project_lock(sql: str, *_args: object) -> None:
                self.assertIn("FOR UPDATE", sql)
                lock_order.append("project")

            cursor.execute.side_effect = record_project_lock
        cursor.fetchone.return_value = (published_version_id, "owner-1")
        connection = Mock()
        connection.cursor.return_value = nullcontext(cursor)
        return connection, cursor

    def test_stale_lease_holder_cannot_create_snapshot_or_publish(self) -> None:
        lock_order: list[str] = []
        connection, project_cursor = self._project_connection("new-version", lock_order)
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(
                    self.connection_updated_at, run_missing=True, lock_order=lock_order,
                ),
            ),
            patch.object(scheduler, "execute") as execute,
            patch("agora.data.snapshots.create_csv_snapshot") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot") as create_version,
            patch("agora.content.repository.publish") as publish,
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        self.assertEqual(lock_order, ["project", "dataset", "schedule", "run"])
        project_cursor.execute.assert_called_once()
        execute.assert_not_called()
        create_snapshot.assert_not_called()
        create_version.assert_not_called()
        publish.assert_not_called()

    def test_membership_owner_authority_reads_role_under_lock(self) -> None:
        connection = Mock()
        cursor = Mock()
        cursor.fetchone.return_value = (0,)
        connection.cursor.return_value = nullcontext(cursor)
        with patch.object(scheduler, "query_one", return_value={"role": "owner"}) as query_one:
            allowed = scheduler._has_publish_authority(
                connection, "project-1", "member-owner", owner_id="other-owner"
            )

        self.assertTrue(allowed)
        cursor.execute.assert_called_once()
        self.assertIn("FOR UPDATE", cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1], {"actor_id": "member-owner"})
        self.assertIs(query_one.call_args.args[0], connection)
        self.assertIn("TB_TA_AGORA_MEMBERSHIPS", query_one.call_args.args[1])
        self.assertIn("FOR UPDATE", query_one.call_args.args[1])

    def test_enqueue_due_runs_passes_transaction_connection_to_every_db_helper(self) -> None:
        connection = Mock()
        due = datetime.now(timezone.utc) - timedelta(minutes=31)
        due_schedule = {
            "connection_id": "connection-1",
            "project_id": "project-1",
            "frequency": "interval",
            "interval_minutes": 15,
            "local_time": None,
            "weekdays_json": "[]",
            "day_of_month": None,
            "timezone": "UTC",
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "draft",
            "publish_approved_by": None,
            "created_by": "owner-1",
            "next_run_at": due,
        }
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", side_effect=[due_schedule, None]) as query_one,
            patch.object(scheduler, "execute") as execute,
        ):
            inserted = scheduler.enqueue_due_runs(limit=10)

        self.assertEqual(inserted, 1)
        self.assertEqual(query_one.call_count, 2)
        for call in query_one.call_args_list:
            self.assertIs(call.args[0], connection)
        self.assertEqual(execute.call_count, 2)
        for call in execute.call_args_list:
            self.assertIs(call.args[0], connection)
        self.assertIn("FOR UPDATE SKIP LOCKED", query_one.call_args_list[0].args[1])
        next_run_at = execute.call_args_list[1].args[2]["next_run_at"]
        self.assertGreater(next_run_at, datetime.now(timezone.utc))

    def test_claim_captures_publication_fence_on_same_transaction_connection(self) -> None:
        connection = Mock()
        published_at = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        connection_updated_at = datetime(2026, 9, 27, 11, tzinfo=timezone.utc)
        queued_run = {
            "id": "run-1", "connection_id": "connection-1", "project_id": "project-1",
            "attempt_count": 0, "base_version_id": "base-version", "publish_mode": "auto_publish",
            "version_actor_id": "owner-1", "publish_actor_id": "owner-1",
            "requested_by": "owner-1", "is_manual": 0,
        }
        query_rows = iter((
            {"enabled": 1},
            {"run_count": 0},
            {"published_version_id": "published-version"},
            {"updated_at": connection_updated_at},
        ))
        with (
            patch.object(scheduler, "_reap_expired_claim", return_value=False),
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", side_effect=lambda *_args: next(query_rows)) as query_one,
            patch.object(scheduler, "query_all", return_value=[queued_run]) as query_all,
            patch.object(scheduler, "execute", return_value=1) as execute,
            patch.object(scheduler, "_latest_publication_at", return_value=published_at) as latest_publication,
        ):
            claim = scheduler.claim_next_run()

        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim["expected_published_id"], "published-version")
        self.assertEqual(claim["expected_publication_at"], published_at)
        self.assertEqual(claim["connection_updated_at"], connection_updated_at)
        self.assertEqual(query_one.call_count, 4)
        self.assertTrue(all(call.args[0] is connection for call in query_one.call_args_list))
        self.assertIs(query_all.call_args.args[0], connection)
        self.assertIn("NOT EXISTS", query_all.call_args.args[1])
        self.assertIn("ROWNUM <= 100", query_all.call_args.args[1])
        self.assertIs(latest_publication.call_args.args[0], connection)
        self.assertEqual(latest_publication.call_args.args[1], "project-1")
        self.assertIs(execute.call_args.args[0], connection)
        self.assertEqual(execute.call_args.args[2]["expected_publication_at"], published_at)
        self.assertEqual(execute.call_args.args[2]["connection_updated_at"], connection_updated_at)

    def test_changed_publication_pointer_keeps_refresh_as_reviewable_draft(self) -> None:
        lock_order: list[str] = []
        connection, _ = self._project_connection("new-version", lock_order)
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at, lock_order=lock_order),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=("published-snapshot", "old-hash")),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new") as create_version,
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        create_snapshot.assert_called_once()
        create_version.assert_called_once_with(
            connection, "project-1", "new-version", "owner-1", "snapshot-new"
        )
        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "publication_changed")
        self.assertEqual(run_update.args[2]["published"], 0)
        self.assertEqual(lock_order, ["project", "dataset", "schedule", "run"])

    def test_publish_then_rollback_to_same_pointer_still_requires_review(self) -> None:
        connection, _ = self._project_connection("old-version")
        claim_time = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        later_publish_time = datetime(2026, 9, 27, 12, 5, tzinfo=timezone.utc)
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=("published-snapshot", "old-hash")),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=later_publish_time),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new") as create_version,
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(
                self._claim(
                    expected_published_id="old-version",
                    expected_publication_at=claim_time,
                ),
                self.payload,
                "Orders",
            )

        create_snapshot.assert_called_once()
        create_version.assert_called_once_with(
            connection, "project-1", "old-version", "owner-1", "snapshot-new"
        )
        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "publication_changed")
        self.assertEqual(run_update.args[2]["published"], 0)

    def test_auto_publish_success_uses_one_connection_for_snapshot_version_and_publish(self) -> None:
        lock_order: list[str] = []
        connection, _ = self._project_connection("old-version", lock_order)
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at, lock_order=lock_order),
            ) as query_one,
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(
                scheduler, "_has_publish_authority",
                side_effect=lambda *_args: lock_order.append("authority") or True,
            ),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new") as create_version,
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit") as audit,
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        self.assertIs(query_one.call_args.args[0], connection)
        self.assertTrue(execute.call_args_list)
        self.assertTrue(all(call.args[0] is connection for call in execute.call_args_list))
        create_snapshot.assert_called_once()
        self.assertIs(create_snapshot.call_args.args[0], connection)
        create_version.assert_called_once_with(
            connection, "project-1", "old-version", "owner-1", "snapshot-new"
        )
        publish.assert_called_once_with(connection, "project-1", "owner-1", "version-new")
        self.assertIs(audit.call_args.args[0], connection)
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "succeeded")
        self.assertEqual(run_update.args[2]["published"], 1)
        self.assertEqual(lock_order, ["project", "dataset", "schedule", "run", "authority"])

    def test_changed_api_connection_during_fetch_keeps_auto_publish_as_reviewable_draft(self) -> None:
        connection, _ = self._project_connection("old-version")
        newer_config_at = self.connection_updated_at + timedelta(minutes=2)
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(newer_config_at),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new") as create_version,
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        create_snapshot.assert_called_once()
        create_version.assert_called_once_with(
            connection, "project-1", "old-version", "owner-1", "snapshot-new"
        )
        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "api_connection_changed")
        self.assertEqual(run_update.args[2]["published"], 0)

    def test_pausing_auto_publish_while_fetching_revokes_that_run_publication(self) -> None:
        connection, _ = self._project_connection("old-version")
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at, enabled=0),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new"),
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new"),
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "publish_approval_changed")

    def test_pausing_auto_publish_revokes_an_inflight_manual_run_too(self) -> None:
        connection, _ = self._project_connection("old-version")
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at, enabled=0),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new"),
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new"),
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(self._claim(is_manual=1), self.payload, "Orders")

        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "publish_approval_changed")

    def test_changing_owner_approver_while_fetching_revokes_that_run_publication(self) -> None:
        connection, _ = self._project_connection("old-version")
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                side_effect=self._auto_publish_query(self.connection_updated_at, approver="owner-2"),
            ),
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch.object(scheduler, "_has_publish_authority", return_value=True),
            patch("agora.data.snapshots.create_csv_snapshot", return_value="snapshot-new"),
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new"),
            patch("agora.content.repository.publish") as publish,
            patch("agora.core.projects.audit"),
        ):
            scheduler._finish_success(self._claim(), self.payload, "Orders")

        publish.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "needs_review")
        self.assertEqual(run_update.args[2]["error_code"], "publish_approval_changed")

    def test_identical_published_snapshot_is_reused_without_creating_version(self) -> None:
        from agora.data.snapshots import validate_csv

        connection, _ = self._project_connection("current-version")
        digest = validate_csv("api-orders.csv", self.payload).sha256
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", return_value={"id": "run-1"}) as query_one,
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=("snapshot-current", digest)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=None),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch("agora.data.snapshots.create_csv_snapshot") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot") as create_version,
            patch("agora.content.repository.publish") as publish,
        ):
            scheduler._finish_success(
                self._claim(
                    expected_published_id="current-version",
                    source_version_id="current-version",
                ),
                self.payload,
                "Orders",
            )

        create_snapshot.assert_not_called()
        create_version.assert_not_called()
        publish.assert_not_called()
        self.assertIs(query_one.call_args.args[0], connection)
        self.assertTrue(execute.call_args_list)
        self.assertTrue(all(call.args[0] is connection for call in execute.call_args_list))
        run_update = next(
            call for call in execute.call_args_list
            if "unchanged = 1" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["version_id"], "current-version")
        self.assertEqual(run_update.args[2]["snapshot_id"], "snapshot-current")

    def test_unchanged_draft_import_reuses_prior_run_and_passes_connection(self) -> None:
        from agora.data.snapshots import validate_csv

        connection, _ = self._project_connection(None)
        digest = validate_csv("api-orders.csv", self.payload).sha256
        query_rows = iter((
            {"updated_at": self.connection_updated_at},
            {"enabled": 1, "publish_mode": "draft", "publish_approved_by": None},
            {"id": "run-1"},
            {"sha256": digest},
            {"package_id": "package-base"},
            {"package_id": "package-base"},
        ))
        previous_run = {"version_id": "prior-version", "snapshot_id": "prior-snapshot", "published": 0}
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", side_effect=lambda *_args: next(query_rows)) as query_one,
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=previous_run),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch("agora.data.snapshots.create_csv_snapshot") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot") as create_version,
        ):
            scheduler._finish_success(
                self._claim(
                    publish_mode="draft",
                    expected_published_id=None,
                    source_version_id="base-version",
                    base_version_id="base-version",
                ),
                self.payload,
                "Orders",
            )

        self.assertEqual(query_one.call_count, 6)
        self.assertTrue(all(call.args[0] is connection for call in query_one.call_args_list))
        self.assertTrue(execute.call_args_list)
        self.assertTrue(all(call.args[0] is connection for call in execute.call_args_list))
        create_snapshot.assert_not_called()
        create_version.assert_not_called()
        run_update = next(
            call for call in execute.call_args_list
            if "unchanged = 1" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["version_id"], "prior-version")
        self.assertEqual(run_update.args[2]["snapshot_id"], "prior-snapshot")

    def test_same_csv_with_changed_html_package_creates_new_draft_version(self) -> None:
        from agora.data.snapshots import validate_csv

        connection, _ = self._project_connection(None)
        digest = validate_csv("api-orders.csv", self.payload).sha256
        query_rows = iter((
            {"updated_at": self.connection_updated_at},
            {"enabled": 1, "publish_mode": "draft", "publish_approved_by": None},
            {"id": "run-1"},
            {"sha256": digest},
            {"package_id": "base-package"},
            {"package_id": "newer-package"},
        ))
        previous_run = {"version_id": "prior-version", "snapshot_id": "prior-snapshot", "published": 0}
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", side_effect=lambda *_args: next(query_rows)) as query_one,
            patch.object(scheduler, "execute") as execute,
            patch.object(scheduler, "_snapshot_digest", return_value=(None, None)),
            patch.object(scheduler, "_latest_snapshot_run", return_value=previous_run),
            patch.object(scheduler, "_latest_publication_at", return_value=None),
            patch("agora.data.snapshots.create_csv_snapshot") as create_snapshot,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-new") as create_version,
        ):
            scheduler._finish_success(
                self._claim(
                    publish_mode="draft",
                    expected_published_id=None,
                    source_version_id="base-version",
                    base_version_id="base-version",
                ),
                self.payload,
                "Orders",
            )

        self.assertEqual(query_one.call_count, 6)
        self.assertTrue(all(call.args[0] is connection for call in query_one.call_args_list))
        create_snapshot.assert_not_called()
        create_version.assert_called_once_with(
            connection, "project-1", "base-version", "owner-1", "prior-snapshot"
        )
        self.assertTrue(all(call.args[0] is connection for call in execute.call_args_list))
        run_update = next(
            call for call in execute.call_args_list
            if "SET status = :status" in call.args[1]
        )
        self.assertEqual(run_update.args[2]["status"], "succeeded")
        self.assertEqual(run_update.args[2]["version_id"], "version-new")
        self.assertEqual(run_update.args[2]["snapshot_id"], "prior-snapshot")


class SchedulerRouteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    project_id = "project-1"
    connection_id = "connection-1"

    def setUp(self) -> None:
        self.actor = Actor("editor-1", "editor", "Editor", False, "test-csrf-token")
        self.original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[auth.require_actor] = lambda: self.actor
        self.role = "editor"
        self.role_patch = patch.object(api_router, "require_project_role", side_effect=self._require_role)
        self.role_patch.start()
        self.addCleanup(self.role_patch.stop)
        self.addCleanup(self._restore_dependency_overrides)

    def _restore_dependency_overrides(self) -> None:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)

    def _require_role(self, _project_id: str, _actor: Actor, minimum: str = "viewer") -> str:
        rank = {"viewer": 0, "editor": 1, "owner": 2, "admin": 3}
        if rank[self.role] < rank[minimum]:
            raise HTTPException(status_code=403, detail={"code": "project_forbidden", "message": "forbidden"})
        return self.role

    async def _request(
        self, method: str, path: str, *, payload: dict[str, object] | None = None,
    ) -> tuple[int, object]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = {
            "host": "testserver",
            "accept": "application/json",
            "sec-fetch-site": "same-origin",
            "x-csrf-token": self.actor.csrf_token,
        }
        if payload is not None:
            headers["content-type"] = "application/json"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        sent_request = False
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"") for message in messages if message["type"] == "http.response.body"
        )
        return int(start["status"]), json.loads(response_body) if response_body else None

    def _auto_publish_payload(self, *, enabled: bool = True) -> dict[str, object]:
        return {
            "enabled": enabled,
            "frequency": "interval",
            "interval_minutes": 60,
            "local_time": None,
            "weekdays": [],
            "day_of_month": None,
            "timezone": "UTC",
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "auto_publish",
        }

    async def test_editor_cannot_enable_auto_publish_schedule(self) -> None:
        with (
            patch.object(scheduler, "get_schedule", return_value=None),
            patch.object(scheduler, "connection_exists", return_value=True),
            patch.object(scheduler, "save_schedule") as save_schedule,
        ):
            status, response = await self._request(
                "PUT",
                f"/api/projects/{self.project_id}/api-datasets/{self.connection_id}/schedule",
                payload=self._auto_publish_payload(),
            )

        self.assertEqual(status, 403)
        self.assertEqual(response["error"]["code"], "auto_publish_owner_required")
        save_schedule.assert_not_called()

    async def test_editor_cannot_run_owner_approved_auto_publish_schedule(self) -> None:
        with (
            patch.object(scheduler, "connection_exists", return_value=True),
            patch.object(scheduler, "get_schedule", return_value={"publish_mode": "auto_publish"}),
            patch.object(scheduler, "queue_manual_run") as queue_manual_run,
        ):
            status, response = await self._request(
                "POST",
                f"/api/projects/{self.project_id}/api-datasets/{self.connection_id}/run-now",
            )

        self.assertEqual(status, 403)
        self.assertEqual(response["error"]["code"], "auto_publish_owner_required")
        queue_manual_run.assert_not_called()

    async def test_owner_cannot_run_paused_auto_publish_schedule(self) -> None:
        self.role = "owner"
        connection = Mock()
        with (
            patch.object(scheduler, "connection_exists", return_value=True),
            patch.object(scheduler, "get_schedule", return_value={"enabled": False, "publish_mode": "auto_publish"}),
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(
                scheduler, "query_one",
                return_value={
                    "connection_id": self.connection_id,
                    "enabled": 0,
                    "base_version_id": BASE_VERSION_ID,
                    "publish_mode": "auto_publish",
                    "publish_approved_by": self.actor.id,
                    "created_by": self.actor.id,
                },
            ),
            patch.object(scheduler, "execute") as execute,
        ):
            status, response = await self._request(
                "POST",
                f"/api/projects/{self.project_id}/api-datasets/{self.connection_id}/run-now",
            )

        self.assertEqual(status, 409)
        self.assertEqual(response["error"]["code"], "auto_publish_schedule_disabled")
        execute.assert_not_called()

    async def test_editor_can_pause_existing_auto_publish_schedule(self) -> None:
        existing = {
            "enabled": True,
            "frequency": "interval",
            "interval_minutes": 60,
            "local_time": None,
            "weekdays": [],
            "day_of_month": None,
            "timezone": "UTC",
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "auto_publish",
        }
        expected_schedule = {"connection_id": self.connection_id, **existing, "enabled": False}
        payload = self._auto_publish_payload(enabled=False)
        with (
            patch.object(scheduler, "get_schedule", return_value=existing),
            patch.object(scheduler, "connection_exists", return_value=True),
            patch.object(scheduler, "save_schedule", return_value=expected_schedule) as save_schedule,
            patch.object(api_router, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(api_router, "audit"),
        ):
            status, response = await self._request(
                "PUT",
                f"/api/projects/{self.project_id}/api-datasets/{self.connection_id}/schedule",
                payload=payload,
            )

        self.assertEqual(status, 200)
        self.assertFalse(response["schedule"]["enabled"])
        save_schedule.assert_called_once_with(
            self.project_id, self.connection_id, self.actor.id, payload, "editor"
        )


class SchedulerApproverPersistenceTests(unittest.TestCase):
    def test_editor_pause_preserves_owner_auto_publish_approval(self) -> None:
        connection = Mock()
        existing = {
            "connection_id": "connection-1",
            "enabled": 1,
            "frequency": "interval",
            "interval_minutes": 60,
            "local_time": None,
            "weekdays_json": "[]",
            "day_of_month": None,
            "timezone": "UTC",
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "auto_publish",
            "publish_approved_by": "owner-1",
        }
        final_row = {
            **existing,
            "enabled": 0,
            "next_run_at": None,
            "last_run_at": None,
            "created_at": None,
            "updated_at": None,
        }
        query_rows = iter((
            {"id": "connection-1"},
            {"id": BASE_VERSION_ID},
            existing,
            final_row,
        ))
        payload = {
            "enabled": False,
            "frequency": "interval",
            "interval_minutes": 60,
            "local_time": None,
            "weekdays": [],
            "day_of_month": None,
            "timezone": "UTC",
            "base_version_id": BASE_VERSION_ID,
            "publish_mode": "auto_publish",
        }
        with (
            patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
            patch.object(scheduler, "query_one", side_effect=lambda *_args: next(query_rows)) as query_one,
            patch.object(scheduler, "execute") as execute,
        ):
            saved = scheduler.save_schedule(
                "project-1", "connection-1", "editor-1", payload, role="editor"
            )

        self.assertEqual(query_one.call_count, 4)
        self.assertTrue(all(call.args[0] is connection for call in query_one.call_args_list))
        update = next(call for call in execute.call_args_list if "UPDATE TB_TA_AGORA_API_DATASET_SCHEDULES" in call.args[1])
        self.assertEqual(update.args[2]["publish_approved_by"], "owner-1")
        self.assertEqual(saved["publish_mode"], "auto_publish")
        self.assertFalse(saved["enabled"])

    def test_failure_details_are_safe_and_get_only_errors_are_retryable(self) -> None:
        from agora.data.api_datasets import ApiDatasetError

        code, message, retryable = scheduler._safe_failure(
            ApiDatasetError("api_timeout", "request URL contained token=private-value")
        )
        self.assertEqual(code, "api_timeout")
        self.assertNotIn("private-value", message)
        self.assertTrue(retryable)

        get_claim = {
            "id": "run-1", "project_id": "project-1", "connection_id": "connection-1",
            "claim_token": "lease-1", "method": "GET",
        }
        post_claim = {
            "id": "run-1", "project_id": "project-1", "connection_id": "connection-1",
            "claim_token": "lease-1", "method": "POST",
        }
        for claim, expected_status in ((get_claim, "queued"), (post_claim, "failed")):
            connection = Mock()
            executions: list[tuple[str, dict[str, object]]] = []
            lock_order: list[str] = []

            def capture_execute(_conn: object, sql: str, params: dict[str, object]) -> None:
                executions.append((sql, params))

            def capture_query(_conn: object, sql: str, _params: dict[str, object]) -> dict[str, object]:
                if "TB_TA_AGORA_API_DATASET_SCHEDULES" in sql:
                    lock_order.append("schedule")
                    return {"connection_id": "connection-1"}
                if "TB_TA_AGORA_API_DATASET_RUNS" in sql:
                    lock_order.append("run")
                    return {"attempt_count": 1}
                self.fail(f"Unexpected failure query: {sql}")

            with (
                patch.object(scheduler, "transaction", side_effect=lambda: nullcontext(connection)),
                patch.object(scheduler, "query_one", side_effect=capture_query) as query_one,
                patch.object(scheduler, "execute", side_effect=capture_execute),
            ):
                scheduler._record_failure(claim, code, message, retryable=True)

            self.assertEqual(lock_order, ["schedule", "run"])
            self.assertTrue(all(call.args[0] is connection for call in query_one.call_args_list))
            self.assertTrue(executions)
            self.assertIn(f"status = '{expected_status}'", executions[0][0])
            self.assertNotIn("private-value", str(executions))


if __name__ == "__main__":
    unittest.main()
