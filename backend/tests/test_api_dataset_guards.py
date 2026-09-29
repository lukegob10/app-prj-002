"""Offline regression checks for API URL, transport, and tabular bounds."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from agora.data import api_datasets


class ApiDatasetGuardTests(unittest.TestCase):
    def test_url_must_be_https_and_cannot_embed_user_credentials(self) -> None:
        for url in (
            "http://api.example.test/items",
            "https://user:password@api.example.test/items",
            "https://api.example.test/items#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(api_datasets.ApiDatasetError):
                api_datasets.validate_url(url)

    def test_private_dns_answer_is_blocked(self) -> None:
        answer = (None, None, None, "api.example.test", ("127.0.0.1", 443))
        with patch.object(api_datasets.socket, "getaddrinfo", return_value=[answer]):
            with self.assertRaises(api_datasets.ApiDatasetError) as raised:
                api_datasets._resolve_public_ip("api.example.test", 443)
        self.assertEqual(raised.exception.code, "api_destination_blocked")
        self.assertNotIn("127.0.0.1", str(raised.exception))

    def test_redirect_is_rejected_and_never_followed(self) -> None:
        response = Mock(status=302)
        response.getheader.return_value = "https://169.254.169.254/latest/meta-data"
        connection = Mock()
        connection.sock = None
        connection.getresponse.return_value = response
        with (
            patch.object(api_datasets, "_resolve_public_ip", return_value="93.184.216.34"),
            patch.object(api_datasets, "_PinnedHTTPSConnection", return_value=connection),
        ):
            with self.assertRaises(api_datasets.ApiDatasetError) as raised:
                api_datasets._request_json(
                    "https://api.example.test/items?token=hidden", "GET", {}, None
                )
        self.assertEqual(raised.exception.code, "api_response_error")
        self.assertNotIn("169.254.169.254", str(raised.exception))
        connection.getresponse.assert_called_once()
        response.read1.assert_not_called()

    def test_response_size_is_checked_before_reading_body(self) -> None:
        response = Mock(status=200)
        response.getheader.return_value = str(api_datasets.MAX_RESPONSE_BYTES + 1)
        connection = Mock()
        connection.sock = None
        connection.getresponse.return_value = response
        with (
            patch.object(api_datasets, "_resolve_public_ip", return_value="93.184.216.34"),
            patch.object(api_datasets, "_PinnedHTTPSConnection", return_value=connection),
        ):
            with self.assertRaises(api_datasets.ApiDatasetError) as raised:
                api_datasets._request_json("https://api.example.test/items", "GET", {}, None)
        self.assertEqual(raised.exception.code, "api_response_too_large")
        response.read1.assert_not_called()

    def test_total_deadline_applies_between_response_chunks(self) -> None:
        response = Mock(status=200)
        response.getheader.return_value = None
        response.read1.return_value = b"{\"data\":["
        connection = Mock()
        connection.sock = None
        connection.getresponse.return_value = response
        clock = patch.object(api_datasets.time, "monotonic", side_effect=[100.0, 100.0, 100.0, 100.0, 121.0])
        with (
            clock,
            patch.object(api_datasets, "_resolve_public_ip", return_value="93.184.216.34"),
            patch.object(api_datasets, "_PinnedHTTPSConnection", return_value=connection),
        ):
            with self.assertRaises(api_datasets.ApiDatasetError) as raised:
                api_datasets._request_json("https://api.example.test/items", "GET", {}, None)
        self.assertEqual(raised.exception.code, "api_timeout")
        response.read1.assert_called_once()

    def test_fields_with_case_collisions_are_rejected(self) -> None:
        with self.assertRaises(api_datasets.ApiDatasetError) as raised:
            api_datasets._csv_for_records([{"Item": 1}, {"item": 2}])
        self.assertEqual(raised.exception.code, "api_records_invalid")


if __name__ == "__main__":
    unittest.main()
