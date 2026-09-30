"""Oracle pool behavior without a live database."""

from __future__ import annotations

import os
import unittest
from unittest.mock import call, patch

from agora.core import db


class _Connection:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.ping_count = 0

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, sql: str) -> None:
                connection.ping_count += 1

            def close(self) -> None:
                pass

        return Cursor()

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1


class OraclePoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connections: list[_Connection] = []
        env = patch.dict(
            os.environ,
            {
                "ENV": "PROD",
                "AGORA_DB_POOL_SIZE": "1",
                "AGORA_DB_POOL_MAX_OVERFLOW": "0",
                "AGORA_DB_POOL_TIMEOUT": "1",
                "AGORA_DB_POOL_RECYCLE": "1800",
                "AGORA_DB_POOL_PRE_PING": "1",
                "AGORA_DB_POOL_USE_LIFO": "1",
            },
        )
        env.start()
        self.addCleanup(env.stop)

        pools = patch.object(db, "_pools", {})
        pools.start()
        self.addCleanup(pools.stop)

        treasury = patch.object(db, "TAConnection")
        self.treasury = treasury.start()
        self.addCleanup(treasury.stop)
        self.treasury.return_value.connect.side_effect = self._new_connection

        self.pool = db._pool("PROD")
        self.addCleanup(self.pool.dispose)

    def _new_connection(self) -> _Connection:
        connection = _Connection()
        self.connections.append(connection)
        return connection

    def test_checkout_reuses_treasury_connection_and_resets_it(self) -> None:
        with db.connection() as first:
            raw = first.dbapi_connection
        self.assertEqual(raw.close_count, 0)

        with db.connection() as second:
            self.assertIs(second.dbapi_connection, raw)

        self.treasury.assert_called_once_with(env="PROD")
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(raw.ping_count, 1)
        self.assertGreaterEqual(raw.rollback_count, 2)
        self.assertEqual(raw.close_count, 0)

    def test_transaction_keeps_commit_and_rollback_behavior(self) -> None:
        with db.transaction():
            pass
        raw = self.connections[0]
        self.assertEqual(raw.commit_count, 1)

        with self.assertRaisesRegex(RuntimeError, "failed"):
            with db.transaction():
                raise RuntimeError("failed")
        self.assertEqual(raw.commit_count, 1)
        self.assertGreaterEqual(raw.rollback_count, 1)

    def test_pool_limit_times_out_as_storage_unavailable(self) -> None:
        with db.connection():
            with self.assertRaises(db.StorageUnavailable):
                with db.connection():
                    pass
        self.assertEqual(len(self.connections), 1)

    def test_dev_and_prod_do_not_share_a_pool(self) -> None:
        with db.connection():
            pass
        with patch.dict(os.environ, {"ENV": "DEV"}):
            with db.connection():
                pass
        self.addCleanup(db._pools["DEV"].dispose)

        self.assertIsNot(db._pools["DEV"], db._pools["PROD"])
        self.assertEqual(self.treasury.call_args_list, [call(env="PROD"), call(env="DEV")])
        self.assertEqual(len(self.connections), 2)


if __name__ == "__main__":
    unittest.main()
