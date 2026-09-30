"""Project responses include the owner's display username for canonical URLs."""

import unittest
from contextlib import nullcontext
from unittest.mock import patch

from agora.core import projects
from agora.core.auth import Actor


class ProjectOwnerUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = {
            "id": "project-1",
            "name": "Dashboard",
            "description": None,
            "owner_id": "owner-1",
            "owner_username": "Owner.Name",
            "created_at": "created",
            "updated_at": "updated",
            "allow_viewer_writes": 0,
            "published_version_id": None,
        }

    def test_single_project_includes_owner_username(self) -> None:
        with patch.object(projects, "query_one", return_value=self.project) as query_one:
            result = projects.get_project(object(), "project-1")

        self.assertEqual(result["owner_username"], "Owner.Name")
        self.assertIn("JOIN TB_TA_AGORA_USERS owner", query_one.call_args.args[1])

    def test_shared_project_list_uses_owners_username(self) -> None:
        actor = Actor("viewer-1", "viewer", "Viewer", False, "csrf")
        with (
            patch.object(projects, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(projects, "query_all", return_value=[{**self.project, "role": "viewer"}]) as query_all,
        ):
            result = projects.list_projects(actor)

        self.assertEqual(result["shared"][0]["owner_username"], "Owner.Name")
        self.assertIn("JOIN TB_TA_AGORA_USERS owner", query_all.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
