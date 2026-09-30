"""Projects and dashboard uploads work without optional metadata or CSV data."""

import unittest
from contextlib import nullcontext
from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi import UploadFile

from agora.content import router as content_router
from agora.core import migrate_optional_project_description, projects
from agora.core.auth import Actor
from agora.data import snapshots


class OptionalProjectContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = Actor("account-1", "owner", "Owner", False, "csrf")

    def test_project_creation_accepts_omitted_or_blank_description(self) -> None:
        for payload in ({"name": "Dashboard"}, {"name": "Dashboard", "description": "  "}):
            with self.subTest(payload=payload):
                with (
                    patch.object(projects, "transaction", side_effect=lambda: nullcontext(object())),
                    patch.object(projects, "execute") as execute,
                    patch.object(projects, "audit"),
                    patch.object(projects, "get_project", return_value={"id": "project-1"}),
                ):
                    project = projects.create_project(payload, self.actor)

                self.assertEqual(project["role"], "owner")
                self.assertIsNone(execute.call_args_list[0].args[2]["description"])

    def test_html_upload_succeeds_without_csv(self) -> None:
        package = UploadFile(filename="dashboard.html", file=BytesIO(b"<!doctype html><title>Dashboard</title>"))
        version = {"id": "version-1", "package_sha256": "digest"}
        with (
            patch.object(content_router, "require_project_role"),
            patch.object(content_router, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(content_router, "create_package_version", return_value="version-1") as create_version,
            patch.object(content_router, "get_version", return_value=version),
            patch.object(content_router, "audit"),
            patch.object(snapshots, "get_bound_snapshot_id", return_value=None),
            patch.object(snapshots, "create_csv_snapshot") as create_csv,
        ):
            result = content_router.upload_version("project-1", package, None, self.actor)

        self.assertEqual(result, {**version, "csv_snapshot_id": None})
        self.assertEqual(create_version.call_args.args[1:3], ("project-1", "account-1"))
        create_csv.assert_not_called()

    def test_existing_required_description_is_migrated_once(self) -> None:
        conn = MagicMock()
        with (
            patch.object(migrate_optional_project_description, "connection", side_effect=lambda: nullcontext(conn)),
            patch.object(migrate_optional_project_description, "query_one", return_value={"nullable": "N"}),
        ):
            self.assertTrue(migrate_optional_project_description.migrate())

        execute = conn.cursor.return_value.__enter__.return_value.execute
        execute.assert_called_once()
        self.assertIn("MODIFY (description NULL)", execute.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
