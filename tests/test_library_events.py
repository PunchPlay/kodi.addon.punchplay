from __future__ import annotations

import importlib
import json
import os
import sys
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
        executeJSONRPC=lambda payload: "{}",
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

library_events = importlib.import_module("library_events")
pull_sync = importlib.import_module("pull_sync")


class ParseVideoLibraryUpdateTests(unittest.TestCase):
    def test_parses_flat_payload(self) -> None:
        event = library_events.parse_video_library_update(
            json.dumps({"id": 5, "type": "movie", "playcount": 2})
        )
        self.assertEqual(
            event, {"item_type": "movie", "library_id": 5, "playcount": 2}
        )

    def test_parses_nested_item_payload(self) -> None:
        event = library_events.parse_video_library_update(
            json.dumps({"item": {"id": 9, "type": "episode"}, "playcount": 1})
        )
        self.assertEqual(
            event, {"item_type": "episode", "library_id": 9, "playcount": 1}
        )

    def test_ignores_metadata_only_updates(self) -> None:
        self.assertIsNone(
            library_events.parse_video_library_update(
                json.dumps({"id": 5, "type": "movie"})
            )
        )

    def test_parses_unwatch_toggles(self) -> None:
        # Still parsed (not None) — LiveWatchedSync needs to see it to
        # cancel a pending watched toggle for the same item, even though
        # it's never queued for upload itself.
        event = library_events.parse_video_library_update(
            json.dumps({"id": 5, "type": "movie", "playcount": 0})
        )
        self.assertEqual(
            event, {"item_type": "movie", "library_id": 5, "playcount": 0}
        )

    def test_ignores_scan_updates(self) -> None:
        self.assertIsNone(
            library_events.parse_video_library_update(
                json.dumps({"id": 5, "type": "movie", "playcount": 1, "added": True})
            )
        )
        self.assertIsNone(
            library_events.parse_video_library_update(
                json.dumps(
                    {"id": 5, "type": "movie", "playcount": 1, "transaction": True}
                )
            )
        )

    def test_ignores_other_types_and_garbage(self) -> None:
        self.assertIsNone(
            library_events.parse_video_library_update(
                json.dumps({"id": 5, "type": "tvshow", "playcount": 1})
            )
        )
        self.assertIsNone(library_events.parse_video_library_update("not json"))
        self.assertIsNone(library_events.parse_video_library_update(None))


class LiveWatchedSyncTests(unittest.TestCase):
    def _push(self, sync: "library_events.LiveWatchedSync", **payload) -> None:
        sync.push_update(json.dumps(payload))

    def test_events_wait_for_debounce(self) -> None:
        sync = library_events.LiveWatchedSync()
        self._push(sync, id=1, type="movie", playcount=1)

        # Immediately after the push nothing is due…
        self.assertEqual(sync.pop_due_events([]), [])
        self.assertEqual(sync.pending_count(), 1)

        # …but once the debounce window passes it pops (and is consumed).
        import time as _time

        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["library_id"], 1)
        self.assertEqual(sync.pending_count(), 0)

    def test_repeated_toggles_coalesce_to_latest(self) -> None:
        sync = library_events.LiveWatchedSync()
        self._push(sync, id=1, type="movie", playcount=1)
        self._push(sync, id=1, type="movie", playcount=3)

        self.assertEqual(sync.pending_count(), 1)
        import time as _time

        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        self.assertEqual(due[0]["playcount"], 3)

    def test_unwatch_within_debounce_window_cancels_pending_watch(self) -> None:
        # Mark watched, then reverse it before the debounce window pops —
        # the reversed watch must not still upload once the window elapses.
        sync = library_events.LiveWatchedSync()
        self._push(sync, id=1, type="movie", playcount=1)
        self._push(sync, id=1, type="movie", playcount=0)

        self.assertEqual(sync.pending_count(), 0)
        import time as _time

        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        self.assertEqual(due, [])

    def test_unwatch_with_nothing_pending_is_a_noop(self) -> None:
        sync = library_events.LiveWatchedSync()
        self._push(sync, id=1, type="movie", playcount=0)

        self.assertEqual(sync.pending_count(), 0)

    def test_pull_applied_items_are_suppressed(self) -> None:
        sync = library_events.LiveWatchedSync()
        sync.record_pull_applied({("movie", 1)})
        self._push(sync, id=1, type="movie", playcount=1)
        self._push(sync, id=2, type="movie", playcount=1)

        import time as _time

        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        self.assertEqual([event["library_id"] for event in due], [2])

    def test_recently_played_items_are_suppressed(self) -> None:
        import time as _time

        sync = library_events.LiveWatchedSync()
        self._push(sync, id=7, type="episode", playcount=1)

        recent = [("episode", 7, _time.monotonic())]
        due = sync.pop_due_events(
            recent, now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        self.assertEqual(due, [])
        # Suppressed events are consumed, not retried forever.
        self.assertEqual(sync.pending_count(), 0)

    def test_later_manual_watch_is_not_suppressed_by_recent_playback(self) -> None:
        import time as _time

        sync = library_events.LiveWatchedSync()
        played_at = _time.monotonic() - library_events.LIVE_SYNC_ECHO_MATCH_SECS - 1
        self._push(sync, id=7, type="episode", playcount=1)

        due = sync.pop_due_events(
            [("episode", 7, played_at)],
            now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1,
        )

        self.assertEqual([event["library_id"] for event in due], [7])

    def test_later_manual_watch_is_not_suppressed_by_pull_sync(self) -> None:
        import time as _time

        sync = library_events.LiveWatchedSync()
        sync._pull_applied[("movie", 8)] = (  # pylint: disable=protected-access
            _time.monotonic() - library_events.LIVE_SYNC_ECHO_MATCH_SECS - 1
        )
        self._push(sync, id=8, type="movie", playcount=1)

        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )

        self.assertEqual([event["library_id"] for event in due], [8])

    def test_requeued_event_waits_for_debounce_and_keeps_newer_update(self) -> None:
        import time as _time

        sync = library_events.LiveWatchedSync()
        self._push(sync, id=7, type="movie", playcount=1)
        due = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DEBOUNCE_SECS + 1
        )
        sync.requeue_events(due)

        self.assertEqual(sync.pop_due_events([]), [])
        self.assertEqual(sync.pending_count(), 1)

        # A new Kodi update for the same item replaces the retry. Requeueing
        # the older event again must not overwrite the newer playcount.
        self._push(sync, id=7, type="movie", playcount=3)
        sync.requeue_events(due)
        retried = sync.pop_due_events(
            [], now=_time.monotonic() + library_events.LIVE_SYNC_DETAIL_RETRY_SECS + 1
        )
        self.assertEqual(len(retried), 1)
        self.assertEqual(retried[0]["playcount"], 3)


class BuildImportEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_rpc = library_events._rpc

    def tearDown(self) -> None:
        library_events._rpc = self.original_rpc

    def test_builds_movie_entry_from_details(self) -> None:
        def _fake_rpc(method, params):
            assert method == "VideoLibrary.GetMovieDetails"
            assert params["movieid"] == 5
            return {
                "moviedetails": {
                    "title": "Inception",
                    "year": 2010,
                    "uniqueid": {"tmdb": "27205", "imdb": "tt1375666"},
                    "lastplayed": "2026-07-09 20:15:00",
                    "genre": ["Sci-Fi"],
                }
            }

        library_events._rpc = _fake_rpc
        entry = library_events.build_import_entry(
            {"item_type": "movie", "library_id": 5, "playcount": 2}
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["media_type"], "movie")
        self.assertEqual(entry["title"], "Inception")
        self.assertEqual(entry["tmdb_id"], 27205)
        self.assertEqual(entry["imdb_id"], "tt1375666")
        self.assertEqual(entry["playcount"], 2)
        self.assertTrue(entry["watched_at"].endswith("Z"))
        self.assertNotIn("anime", entry)

    def test_builds_episode_entry_and_flags_anime(self) -> None:
        def _fake_rpc(method, params):
            assert method == "VideoLibrary.GetEpisodeDetails"
            assert params["episodeid"] == 100
            return {
                "episodedetails": {
                    "showtitle": "Sousou no Frieren",
                    "season": 1,
                    "episode": 7,
                    "uniqueid": {"tmdb": "209867"},
                    "lastplayed": "",
                    "genre": ["Anime", "Fantasy"],
                }
            }

        library_events._rpc = _fake_rpc
        entry = library_events.build_import_entry(
            {"item_type": "episode", "library_id": 100, "playcount": 1}
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry["media_type"], "episode")
        self.assertEqual(entry["season"], 1)
        self.assertEqual(entry["episode"], 7)
        self.assertTrue(entry["anime"])
        self.assertNotIn("watched_at", entry)

    def test_returns_none_when_item_missing(self) -> None:
        library_events._rpc = lambda method, params: {}
        self.assertIsNone(
            library_events.build_import_entry(
                {"item_type": "movie", "library_id": 5, "playcount": 1}
            )
        )

    def test_transient_detail_failure_is_retryable(self) -> None:
        library_events._rpc = lambda method, params: (_ for _ in ()).throw(
            RuntimeError("Kodi JSON-RPC unavailable")
        )

        with self.assertRaises(library_events.LibraryDetailLookupError):
            library_events.build_import_entry(
                {"item_type": "movie", "library_id": 5, "playcount": 1}
            )


if __name__ == "__main__":
    unittest.main()
