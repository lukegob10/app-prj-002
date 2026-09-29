"""Regression coverage for Oracle JSON values and bounded CSV previews."""

import json
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

from agora.core.auth import Actor
from agora.data import snapshots
from agora.data import router as data_router


class SnapshotMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.row = {
            "id": "snapshot-1",
            "project_id": "project-1",
            "filename": "sales.csv",
            "row_count": 2,
            "columns_json": ["month", "revenue"],
            "created_at": datetime(2026, 9, 27, tzinfo=timezone.utc),
        }

    def test_list_and_get_accept_native_oracle_json_arrays(self) -> None:
        with (
            patch.object(snapshots, "query_all", return_value=[self.row]),
            patch.object(snapshots, "query_one", return_value=self.row),
        ):
            listed = snapshots.list_csv_snapshots(object(), "project-1")
            found = snapshots.get_csv_snapshot(object(), "project-1", "snapshot-1")

        self.assertEqual(listed[0]["columns"], ["month", "revenue"])
        self.assertEqual(found, listed[0])
        self.assertNotIn("columns_json", listed[0])
        self.assertEqual(listed[0]["created_at"], "2026-09-27T00:00:00+00:00")

    def test_metadata_accepts_json_text_too(self) -> None:
        row = {**self.row, "columns_json": json.dumps(["month", "revenue"])}
        self.assertEqual(snapshots._metadata(row)["columns"], ["month", "revenue"])

    def test_preview_paginates_rows_without_changing_snapshot(self) -> None:
        payload = "month,revenue\r\n" + "".join(f"M{index},{index}\r\n" for index in range(25))
        with (
            patch.object(snapshots, "get_csv_snapshot", return_value={"columns": ["month", "revenue"], "row_count": 25}),
            patch.object(snapshots, "read_csv_snapshot", return_value=payload.encode()),
        ):
            first = snapshots.preview_csv_snapshot(object(), "project-1", "snapshot-1")
            last = snapshots.preview_csv_snapshot(object(), "project-1", "snapshot-1", 3, 10)

        self.assertIsNotNone(first)
        self.assertEqual(first["row_count"], 25)
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["total_pages"], 3)
        self.assertEqual(len(first["rows"]), 10)
        self.assertEqual(first["rows"][0], ["M0", "0"])
        self.assertEqual(last["rows"], [["M20", "20"], ["M21", "21"], ["M22", "22"], ["M23", "23"], ["M24", "24"]])
        self.assertEqual(last["page"], 3)


class SnapshotPreviewRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = Actor("account-1", "editor", "Editor", False, "csrf")

    def test_preview_checks_editor_role_before_reading(self) -> None:
        with (
            patch.object(data_router, "require_project_role", side_effect=HTTPException(403)) as role,
            patch.object(data_router, "connection") as database,
        ):
            with self.assertRaises(HTTPException):
                data_router.preview_snapshot("project-1", "snapshot-1", self.actor)
        role.assert_called_once_with("project-1", self.actor, "editor")
        database.assert_not_called()

    def test_preview_reports_missing_snapshot(self) -> None:
        with (
            patch.object(data_router, "require_project_role"),
            patch.object(data_router, "connection", side_effect=lambda: nullcontext(object())),
            patch.object(snapshots, "preview_csv_snapshot", return_value=None),
        ):
            with self.assertRaises(HTTPException) as error:
                data_router.preview_snapshot("project-1", "missing", self.actor)
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
