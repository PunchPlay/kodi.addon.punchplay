from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import types
import unittest

LIB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "lib")
)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = types.SimpleNamespace(
        LOGDEBUG=0,
        LOGINFO=1,
        log=lambda *args, **kwargs: None,
    )

if "xbmcaddon" not in sys.modules:
    class _Addon:
        def getAddonInfo(self, key: str) -> str:
            return ""

        def getLocalizedString(self, message_id: int) -> str:
            return str(message_id)

    sys.modules["xbmcaddon"] = types.SimpleNamespace(
        Addon=lambda *args, **kwargs: _Addon()
    )

if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = types.SimpleNamespace(translatePath=lambda value: value)

constants = importlib.import_module("constants")
cache_module = importlib.import_module("cache")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-cache-tests-")
        self.original_get_profile_dir = cache_module.get_profile_dir
        self.original_max_items = cache_module.OFFLINE_QUEUE_MAX_ITEMS
        cache_module.get_profile_dir = lambda: self.temp_dir

    def tearDown(self) -> None:
        cache_module.get_profile_dir = self.original_get_profile_dir
        cache_module.OFFLINE_QUEUE_MAX_ITEMS = self.original_max_items
        shutil.rmtree(self.temp_dir)

    def test_pending_queue_table_migrates_old_schema(self) -> None:
        db_path = os.path.join(self.temp_dir, "punchplay.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE identifier_cache (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE pending_scrobbles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )

        cache = cache_module.Cache()
        with cache._connect() as conn:  # pylint: disable=protected-access
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(pending_scrobbles)")
            }

        self.assertIn("attempt_count", columns)
        self.assertIn("last_attempt_at", columns)
        self.assertIn("last_error", columns)
        with cache._connect() as conn:  # pylint: disable=protected-access
            identifier_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(identifier_cache)")
            }
        self.assertIn("expires_at", identifier_columns)

    def test_queue_prefers_dropping_progress_before_stop(self) -> None:
        cache_module.OFFLINE_QUEUE_MAX_ITEMS = 2
        cache = cache_module.Cache()

        cache.enqueue_scrobble(constants.SCROBBLE_STOP_ENDPOINT, {"event_id": "stop"})
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "progress-1"},
        )
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "progress-2"},
        )

        pending = cache.get_pending_scrobbles()
        endpoints = [item["endpoint"] for item in pending]
        event_ids = [item["payload"].get("event_id") for item in pending]

        self.assertIn(constants.SCROBBLE_STOP_ENDPOINT, endpoints)
        self.assertIn("stop", event_ids)
        self.assertIn("progress-2", event_ids)
        self.assertNotIn("progress-1", event_ids)

    def test_retry_metadata_is_recorded(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "progress-1"},
        )
        pending = cache.get_pending_scrobbles()
        scrobble_id = int(pending[0]["id"])

        cache.mark_pending_scrobble_attempt(scrobble_id, "HTTP 500")

        updated = cache.get_pending_scrobbles()[0]
        self.assertEqual(updated["attempt_count"], 1)
        self.assertEqual(updated["last_error"], "HTTP 500")
        self.assertIsNotNone(updated["last_attempt_at"])

    def test_queue_endpoint_summary_counts_entries(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobble(constants.SCROBBLE_PROGRESS_ENDPOINT, {"event_id": "progress"})
        cache.enqueue_scrobble(constants.SCROBBLE_STOP_ENDPOINT, {"event_id": "stop"})

        summary = cache.get_queue_endpoint_summary()

        self.assertEqual(summary[constants.SCROBBLE_PROGRESS_ENDPOINT], 1)
        self.assertEqual(summary[constants.SCROBBLE_STOP_ENDPOINT], 1)


if __name__ == "__main__":
    unittest.main()
