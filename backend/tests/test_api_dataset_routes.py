"""API dataset persistence and route checks without live Oracle or HTTP access."""

from __future__ import annotations

import json
import os
import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException

from agora.core import auth
from agora.core.auth import Actor
from agora.data import api_datasets
from agora.data import api_router
from agora.main import app


class ApiDatasetPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = Fernet.generate_key().decode("ascii")
        self.environment = patch.dict(os.environ, {"API_DATA_ENCRYPTION_KEY": self.key})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _memory_database(self) -> tuple[dict[str, object], Mock, Mock]:
        stored: dict[str, object] = {}

        def query_one(_conn: object, sql: str, _params: dict[str, object]) -> dict[str, object] | None:
            normalized = " ".join(sql.lower().split())
            if "lower(name)" in normalized:
                return None
            if not stored:
                return None
            if "select id, url, headers_json, body_json" in normalized:
                return {key: stored[key] for key in ("id", "url", "headers_json", "body_json")}
            return {
                "id": stored["id"],
                "name": stored["name"],
                "url": stored["url"],
                "method": stored["method"],
                "headers_json": stored["headers_json"],
                "body_json": stored["body_json"],
                "records_path": stored["records_path"],
                "updated_at": None,
            }

        def execute(_conn: object, _sql: str, params: dict[str, object]) -> None:
            stored.update(params)

        query_mock = Mock(side_effect=query_one)
        execute_mock = Mock(side_effect=execute)
        return stored, query_mock, execute_mock

    def test_connection_secrets_are_encrypted_at_rest_and_redacted(self) -> None:
        stored, query_mock, execute_mock = self._memory_database()
        with (
            patch.object(api_datasets, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(api_datasets, "query_one", query_mock),
            patch.object(api_datasets, "execute", execute_mock),
        ):
            public = api_datasets.create_connection(
                "project-1",
                "account-1",
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=URL_SECRET&region=west",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer HEADER_SECRET"},
                    "body": {"access_token": "BODY_SECRET", "active": True},
                    "records_path": "data.items",
                },
            )

        persisted_text = " ".join(str(stored[key]) for key in ("url", "headers_json", "body_json"))
        self.assertNotIn("URL_SECRET", persisted_text)
        self.assertNotIn("HEADER_SECRET", persisted_text)
        self.assertNotIn("BODY_SECRET", persisted_text)
        self.assertEqual(api_datasets._unseal_url(str(stored["url"])),
                         "https://api.example.test/orders?api_key=URL_SECRET&region=west")
        self.assertEqual(api_datasets._unseal_json(str(stored["headers_json"])),
                         {"Authorization": "Bearer HEADER_SECRET"})
        self.assertEqual(api_datasets._unseal_json(str(stored["body_json"])),
                         {"access_token": "BODY_SECRET", "active": True})
        self.assertEqual(public["url"], "https://api.example.test/orders?api_key=&region=")
        self.assertEqual(public["headers"], {"Authorization": ""})
        self.assertEqual(public["secret_headers"], ["Authorization"])
        self.assertIsNone(public["body"])
        self.assertTrue(public["body_saved"])
        self.assertNotIn("SECRET", json.dumps(public))
        query_mock.assert_called()
        execute_mock.assert_called_once()

    def test_blank_redacted_fields_preserve_saved_credentials_and_body(self) -> None:
        stored, query_mock, execute_mock = self._memory_database()
        transaction = patch.object(api_datasets, "transaction", side_effect=lambda: nullcontext(object()))
        query = patch.object(api_datasets, "query_one", query_mock)
        execute = patch.object(api_datasets, "execute", execute_mock)
        with transaction, query, execute:
            api_datasets.create_connection(
                "project-1",
                "account-1",
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=URL_SECRET&region=west",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer HEADER_SECRET"},
                    "body": {"access_token": "BODY_SECRET"},
                },
            )
            public = api_datasets.update_connection(
                "project-1",
                str(stored["id"]),
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=&region=",
                    "method": "POST",
                    "headers": {"Authorization": ""},
                    "body": None,
                },
            )

        self.assertIsNotNone(public)
        self.assertEqual(api_datasets._unseal_url(str(stored["url"])),
                         "https://api.example.test/orders?api_key=URL_SECRET&region=west")
        self.assertEqual(api_datasets._unseal_json(str(stored["headers_json"])),
                         {"Authorization": "Bearer HEADER_SECRET"})
        self.assertEqual(api_datasets._unseal_json(str(stored["body_json"])),
                         {"access_token": "BODY_SECRET"})
        self.assertTrue(public["body_saved"])
        self.assertNotIn("SECRET", json.dumps(public))

        with transaction, query, execute:
            cleared = api_datasets.update_connection(
                "project-1",
                str(stored["id"]),
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=&region=",
                    "method": "POST",
                    "headers": {"Authorization": ""},
                    "body": None,
                    "clear_body": True,
                },
            )
        self.assertIsNotNone(cleared)
        self.assertIsNone(stored["body_json"])
        self.assertFalse(cleared["body_saved"])

    def test_oracle_json_mappings_roundtrip_and_retained_body_is_serialized(self) -> None:
        stored, query_mock, execute_mock = self._memory_database()
        with (
            patch.object(api_datasets, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(api_datasets, "query_one", query_mock),
            patch.object(api_datasets, "execute", execute_mock),
        ):
            api_datasets.create_connection(
                "project-1",
                "account-1",
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=URL_SECRET",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer HEADER_SECRET"},
                    "body": {"access_token": "BODY_SECRET"},
                },
            )
            # Some Oracle driver configurations decode CLOB IS JSON columns to mappings.
            stored["headers_json"] = json.loads(str(stored["headers_json"]))
            stored["body_json"] = json.loads(str(stored["body_json"]))
            public = api_datasets._public_connection(
                {
                    "id": stored["id"],
                    "name": stored["name"],
                    "url": stored["url"],
                    "method": stored["method"],
                    "headers_json": stored["headers_json"],
                    "body_json": stored["body_json"],
                    "records_path": None,
                    "updated_at": None,
                }
            )
            updated = api_datasets.update_connection(
                "project-1",
                str(stored["id"]),
                {
                    "name": "Orders API",
                    "url": "https://api.example.test/orders?api_key=",
                    "method": "POST",
                    "headers": {"Authorization": ""},
                    "body": None,
                },
            )

        self.assertEqual(public["headers"], {"Authorization": ""})
        self.assertTrue(public["body_saved"])
        self.assertIsNotNone(updated)
        self.assertIsInstance(stored["body_json"], str)
        self.assertEqual(api_datasets._unseal_json(stored["body_json"]), {"access_token": "BODY_SECRET"})

    def test_custom_post_payload_uses_mocked_https_transport(self) -> None:
        response = Mock(status=200)
        response.getheader.return_value = None
        response.read1.side_effect = [b'{"data":[{"id":7}]}', b""]
        connection = Mock()
        connection.sock = None
        connection.getresponse.return_value = response
        with (
            patch.object(api_datasets, "_resolve_public_ip", return_value="93.184.216.34"),
            patch.object(api_datasets, "_PinnedHTTPSConnection", return_value=connection) as transport,
        ):
            decoded = api_datasets._request_json(
                "https://api.example.test/v2/items?tenant=acme",
                "POST",
                {"Authorization": "Bearer TEST_TOKEN"},
                {"status": "active"},
            )

        self.assertEqual(decoded, {"data": [{"id": 7}]})
        transport.assert_called_once()
        self.assertEqual(
            transport.call_args.args[:4],
            ("api.example.test", 443, "93.184.216.34", 12),
        )
        self.assertIsInstance(transport.call_args.args[4], float)
        method, path = connection.request.call_args.args[:2]
        body = connection.request.call_args.kwargs["body"]
        headers = connection.request.call_args.kwargs["headers"]
        self.assertEqual((method, path), ("POST", "/v2/items?tenant=acme"))
        self.assertEqual(json.loads(body), {"status": "active"})
        self.assertEqual(headers["Authorization"], "Bearer TEST_TOKEN")


class ApiDatasetRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.actor = Actor("account-1", "editor", "Editor", False, "test-csrf-token")
        self.original_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[auth.require_actor] = lambda: self.actor
        self.role = "editor"
        self.required_roles: list[str] = []
        self.role_patch = patch.object(api_router, "require_project_role", side_effect=self._require_role)
        self.role_patch.start()
        self.addCleanup(self.role_patch.stop)
        self.addCleanup(self._restore_dependency_overrides)

    def _restore_dependency_overrides(self) -> None:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)

    def _require_role(self, _project_id: str, _actor: Actor, minimum: str = "viewer") -> str:
        self.required_roles.append(minimum)
        rank = {"viewer": 0, "editor": 1, "owner": 2, "admin": 3}
        if rank[self.role] < rank[minimum]:
            raise HTTPException(
                status_code=403,
                detail={"code": "project_forbidden", "message": "Your project role does not allow this action."},
            )
        return self.role

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        request_headers = {
            "host": "testserver",
            "accept": "application/json",
            "sec-fetch-site": "same-origin",
        }
        if payload is not None:
            request_headers["content-type"] = "application/json"
        if headers:
            request_headers.update({key.lower(): value for key, value in headers.items()})
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
            "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in request_headers.items()],
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
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return int(start["status"]), json.loads(response_body) if response_body else None

    async def test_list_requires_editor_role(self) -> None:
        with patch.object(api_datasets, "list_connections", return_value=[{"id": "connection-1"}]) as list_connections:
            self.role = "viewer"
            status, response = await self._request("GET", "/api/projects/project-1/api-datasets")
            self.assertEqual(status, 403)
            list_connections.assert_not_called()

            self.role = "editor"
            status, response = await self._request("GET", "/api/projects/project-1/api-datasets")
        self.assertEqual(status, 200)
        self.assertEqual(response, {"connections": [{"id": "connection-1"}]})
        self.assertEqual(self.required_roles, ["editor", "editor"])

    async def test_create_requires_csrf_before_saving(self) -> None:
        payload = {
            "name": "Orders API",
            "url": "https://api.example.test/orders",
            "method": "GET",
            "headers": {},
            "body": None,
        }
        with (
            patch.object(api_datasets, "create_connection", return_value={"id": "connection-1"}) as create_connection,
            patch.object(api_router, "transaction", side_effect=lambda: nullcontext(object())),
            patch.object(api_router, "audit"),
        ):
            status, response = await self._request(
                "POST", "/api/projects/project-1/api-datasets", payload=payload
            )
            self.assertEqual(status, 403)
            create_connection.assert_not_called()

            status, response = await self._request(
                "POST",
                "/api/projects/project-1/api-datasets",
                payload=payload,
                headers={"x-csrf-token": self.actor.csrf_token},
            )
        self.assertEqual(status, 201)
        self.assertEqual(response, {"connection": {"id": "connection-1"}})
        create_connection.assert_called_once_with("project-1", self.actor.id, payload)

    async def test_import_clones_selected_html_version_and_binds_api_snapshot(self) -> None:
        fake_conn = object()
        snapshot = {"id": "snapshot-1", "filename": "api-orders-conn-1.csv", "row_count": 1}
        config = {"id": "connection-1", "name": "Orders", "url": "https://api.example.test/orders"}
        payload = {"base_version_id": "html-version-7"}
        with (
            patch.object(api_router, "connection", side_effect=lambda: nullcontext(fake_conn)),
            patch.object(api_router, "transaction", side_effect=lambda: nullcontext(fake_conn)),
            patch("agora.content.repository.get_version", return_value={"id": "html-version-7"}) as get_version,
            patch("agora.content.repository.create_version_for_snapshot", return_value="version-8") as create_version,
            patch.object(api_datasets, "get_config_for_project", return_value=config) as get_config,
            patch.object(api_datasets, "response_to_csv", return_value=(b"id\r\n7\r\n", ["id"], 1)) as fetch_csv,
            patch.object(api_router.snapshots, "create_csv_snapshot", return_value="snapshot-1") as create_snapshot,
            patch.object(api_router.snapshots, "get_csv_snapshot", return_value=snapshot),
            patch.object(api_router, "audit"),
        ):
            status, response = await self._request(
                "POST",
                "/api/projects/project-1/api-datasets/connection-1/import",
                payload=payload,
                headers={"x-csrf-token": self.actor.csrf_token},
            )

        self.assertEqual(status, 200)
        self.assertEqual(response, {"snapshot": snapshot, "version_id": "version-8"})
        get_version.assert_called_once_with(fake_conn, "project-1", "html-version-7")
        get_config.assert_called_once_with("project-1", "connection-1")
        fetch_csv.assert_called_once_with(config)
        create_snapshot.assert_called_once_with(
            fake_conn,
            "project-1",
            self.actor.id,
            "api-Orders-connecti.csv",
            b"id\r\n7\r\n",
        )
        create_version.assert_called_once_with(
            fake_conn, "project-1", "html-version-7", self.actor.id, "snapshot-1"
        )

    async def test_import_rejects_unknown_html_version_before_fetching_api(self) -> None:
        fake_conn = object()
        with (
            patch.object(api_router, "connection", side_effect=lambda: nullcontext(fake_conn)),
            patch("agora.content.repository.get_version", return_value=None),
            patch.object(api_datasets, "get_config_for_project") as get_config,
            patch.object(api_datasets, "response_to_csv") as fetch_csv,
        ):
            status, response = await self._request(
                "POST",
                "/api/projects/project-1/api-datasets/connection-1/import",
                payload={"base_version_id": "missing-version"},
                headers={"x-csrf-token": self.actor.csrf_token},
            )
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "version_not_found")
        get_config.assert_not_called()
        fetch_csv.assert_not_called()

    async def test_viewer_cannot_test_or_import_saved_api_datasets(self) -> None:
        self.role = "viewer"
        fake_conn = object()
        with (
            patch.object(api_datasets, "test_connection") as test_connection,
            patch.object(api_router, "connection", side_effect=lambda: nullcontext(fake_conn)),
            patch.object(api_router, "transaction", side_effect=lambda: nullcontext(fake_conn)),
            patch("agora.content.repository.get_version", return_value={"id": "html-version-7"}),
            patch("agora.content.repository.create_version_for_snapshot"),
            patch.object(api_datasets, "get_config_for_project") as get_config,
            patch.object(api_datasets, "response_to_csv") as fetch_csv,
            patch.object(api_router.snapshots, "create_csv_snapshot"),
            patch.object(api_router.snapshots, "get_csv_snapshot"),
            patch.object(api_router, "audit"),
        ):
            headers = {"x-csrf-token": self.actor.csrf_token}
            test_status, _ = await self._request(
                "POST",
                "/api/projects/project-1/api-datasets/connection-1/test",
                headers=headers,
            )
            import_status, _ = await self._request(
                "POST",
                "/api/projects/project-1/api-datasets/connection-1/import",
                payload={"base_version_id": "html-version-7"},
                headers=headers,
            )

        self.assertEqual((test_status, import_status), (403, 403))
        test_connection.assert_not_called()
        get_config.assert_not_called()
        fetch_csv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
