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
        LOGWARNING=2,
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
        self.assertIn("event_created_at", columns)
        with cache._connect() as conn:  # pylint: disable=protected-access
            identifier_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(identifier_cache)")
            }
        self.assertIn("expires_at", identifier_columns)
        with cache._connect() as conn:  # pylint: disable=protected-access
            runtime_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(runtime_status)")
            }
        self.assertIn("pull_sync_failure_counts", runtime_columns)
        self.assertIn("pull_sync_context", runtime_columns)

    def test_rating_suppression_migration_clears_old_show_key_format(self) -> None:
        db_path = os.path.join(self.temp_dir, "punchplay.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE rating_suppressions (
                    key TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            # Old format: show:{id-or-title}:{title}:{year} — three colons.
            conn.execute(
                "INSERT INTO rating_suppressions (key, scope, created_at) "
                "VALUES ('show:1396:breaking bad:2008', 'show', 1000)"
            )
            # New format and an unrelated title key both survive.
            conn.execute(
                "INSERT INTO rating_suppressions (key, scope, created_at) "
                "VALUES ('show:the office', 'show', 1000)"
            )
            conn.execute(
                "INSERT INTO rating_suppressions (key, scope, created_at) "
                "VALUES ('title:movie:27205:2010::', 'title', 1000)"
            )

        cache_module.Cache()
        with sqlite3.connect(db_path) as conn:
            keys = {row[0] for row in conn.execute("SELECT key FROM rating_suppressions")}

        self.assertNotIn("show:1396:breaking bad:2008", keys)
        self.assertIn("show:the office", keys)
        self.assertIn("title:movie:27205:2010::", keys)

    def test_rating_suppression_migration_runs_only_once(self) -> None:
        db_path = os.path.join(self.temp_dir, "punchplay.db")
        cache_module.Cache()  # creates the table, runs the migration once

        with sqlite3.connect(db_path) as conn:
            # A real show:{title} key whose title itself contains two colons
            # would otherwise match the old 3-segment pattern forever if the
            # migration weren't gated to run exactly once.
            conn.execute(
                "INSERT INTO rating_suppressions (key, scope, created_at) "
                "VALUES (\"show:grey's anatomy: station 19: crossover\", 'show', 2000)"
            )

        cache_module.Cache()  # must be a no-op for this migration this time
        with sqlite3.connect(db_path) as conn:
            keys = {row[0] for row in conn.execute("SELECT key FROM rating_suppressions")}

        self.assertIn("show:grey's anatomy: station 19: crossover", keys)

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

    def test_pending_scrobble_exists_reflects_deletion(self) -> None:
        # flush_queue() snapshots pending rows, then re-checks each one just
        # before replaying it, so a row deleted by session-ending cleanup
        # (delete_pending_scrobbles_for_session) after the snapshot was
        # taken isn't replayed as a stale event after an authoritative stop.
        cache = cache_module.Cache()
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "progress-1"},
        )
        scrobble_id = int(cache.get_pending_scrobbles()[0]["id"])

        self.assertTrue(cache.pending_scrobble_exists(scrobble_id))

        cache.delete_pending_scrobble(scrobble_id)

        self.assertFalse(cache.pending_scrobble_exists(scrobble_id))

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

    def test_queue_drops_entries_after_attempt_cap(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "poison"},
        )
        cache.enqueue_scrobble(
            constants.SCROBBLE_STOP_ENDPOINT,
            {"event_id": "healthy"},
        )
        poison_id = int(cache.get_pending_scrobbles()[0]["id"])
        with cache._connect() as conn:  # pylint: disable=protected-access
            conn.execute(
                "UPDATE pending_scrobbles SET attempt_count = ? WHERE id = ?",
                (constants.MAX_QUEUE_ATTEMPTS, poison_id),
            )

        dropped = cache.drop_expired_pending_scrobbles()

        self.assertEqual(dropped, 1)
        event_ids = [
            item["payload"].get("event_id") for item in cache.get_pending_scrobbles()
        ]
        self.assertEqual(event_ids, ["healthy"])

    def test_clear_rating_suppressions_reports_count(self) -> None:
        cache = cache_module.Cache()
        cache.set_rating_suppression("title:movie:1", "title")
        cache.set_rating_suppression("show:2", "show")

        self.assertTrue(cache.has_rating_suppression("title:movie:1"))
        self.assertEqual(cache.clear_rating_suppressions(), 2)
        self.assertFalse(cache.has_rating_suppression("title:movie:1"))
        self.assertEqual(cache.clear_rating_suppressions(), 0)

    def test_record_pull_sync_updates_runtime_status(self) -> None:
        cache = cache_module.Cache()
        cache.record_pull_sync("3 watched, 1 resume, 0 unmatched")

        status = cache.get_runtime_status()
        self.assertEqual(
            status["last_pull_sync_summary"], "3 watched, 1 resume, 0 unmatched"
        )
        self.assertIsNotNone(status["last_pull_sync_at"])

    def test_pull_sync_context_change_requires_a_full_sync(self) -> None:
        cache = cache_module.Cache()

        self.assertFalse(cache.ensure_pull_sync_context("watched=1;resume=0"))
        cache.record_pull_sync("watched only")
        checkpoint = cache.get_runtime_status()["last_pull_sync_at"]
        self.assertTrue(cache.ensure_pull_sync_context("watched=1;resume=0"))
        self.assertEqual(cache.get_runtime_status()["last_pull_sync_at"], checkpoint)

        self.assertFalse(cache.ensure_pull_sync_context("watched=1;resume=1"))
        self.assertIsNone(cache.get_runtime_status()["last_pull_sync_at"])

    def test_account_change_clears_pull_sync_checkpoint(self) -> None:
        cache = cache_module.Cache()
        cache.set_account_username("alice")
        cache.ensure_pull_sync_context("watched=1;resume=1")
        cache.record_pull_sync("alice sync")

        cache.set_account_username("bob")

        status = cache.get_runtime_status()
        self.assertEqual(status["account_username"], "bob")
        self.assertIsNone(status["last_pull_sync_at"])
        self.assertFalse(cache.ensure_pull_sync_context("watched=1;resume=1"))

    def test_pending_scrobbles_replay_by_event_time_not_insertion_order(self) -> None:
        # A later event can be persisted before an earlier one that was still
        # in flight (e.g. shutdown draining a queue ahead of a slow request
        # that self-persists once it finally times out). Replay must follow
        # what actually happened, not which write happened to land first.
        cache = cache_module.Cache()
        cache.enqueue_scrobble(
            constants.SCROBBLE_STOP_ENDPOINT,
            {"event_id": "stop", "event_created_at": 2000},
        )
        cache.enqueue_scrobble(
            constants.SCROBBLE_PROGRESS_ENDPOINT,
            {"event_id": "progress", "event_created_at": 1000},
        )

        event_ids = [
            item["payload"].get("event_id") for item in cache.get_pending_scrobbles()
        ]

        self.assertEqual(event_ids, ["progress", "stop"])

    def test_enqueue_scrobbles_batches_in_one_call(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobbles(
            [
                (constants.SCROBBLE_PROGRESS_ENDPOINT, {"event_id": "progress", "event_created_at": 1000}),
                (constants.SCROBBLE_STOP_ENDPOINT, {"event_id": "stop", "event_created_at": 2000}),
            ]
        )

        event_ids = [
            item["payload"].get("event_id") for item in cache.get_pending_scrobbles()
        ]

        self.assertEqual(event_ids, ["progress", "stop"])

    def test_enqueue_scrobbles_noop_for_empty_list(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobbles([])

        self.assertEqual(cache.get_pending_scrobbles(), [])

    def test_record_pull_sync_held_increments_and_resets_on_success(self) -> None:
        cache = cache_module.Cache()

        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 1)
        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 2)

        cache.record_pull_sync("2 watched, 0 resume, 0 unmatched, 0 failed")

        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 1)

    def test_pull_sync_held_counts_are_scoped_per_failed_item(self) -> None:
        cache = cache_module.Cache()

        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 1)
        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 2)
        # A newly failing item starts at one even though movie-a has already
        # consumed two retries, so advancing now cannot silently skip it.
        self.assertEqual(
            cache.record_pull_sync_held({"movie-a", "movie-b"}),
            1,
        )
        # Once movie-b succeeds, movie-a retains its consecutive history.
        self.assertEqual(cache.record_pull_sync_held({"movie-a"}), 4)

    def test_queue_endpoint_summary_counts_entries(self) -> None:
        cache = cache_module.Cache()
        cache.enqueue_scrobble(constants.SCROBBLE_PROGRESS_ENDPOINT, {"event_id": "progress"})
        cache.enqueue_scrobble(constants.SCROBBLE_STOP_ENDPOINT, {"event_id": "stop"})

        summary = cache.get_queue_endpoint_summary()

        self.assertEqual(summary[constants.SCROBBLE_PROGRESS_ENDPOINT], 1)
        self.assertEqual(summary[constants.SCROBBLE_STOP_ENDPOINT], 1)


if __name__ == "__main__":
    unittest.main()
