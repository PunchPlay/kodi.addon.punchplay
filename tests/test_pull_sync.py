from __future__ import annotations

import importlib
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

constants = importlib.import_module("constants")
pull_sync = importlib.import_module("pull_sync")


class DatetimeHelperTests(unittest.TestCase):
    def test_iso_to_epoch_parses_utc_and_offset(self) -> None:
        self.assertEqual(constants.iso_to_epoch("1970-01-01T00:00:00Z"), 0.0)
        self.assertEqual(constants.iso_to_epoch("1970-01-01T01:00:00+01:00"), 0.0)
        # JS Date.toISOString() shape (milliseconds).
        self.assertEqual(constants.iso_to_epoch("1970-01-01T00:00:30.500Z"), 30.5)

    def test_iso_to_epoch_rejects_garbage(self) -> None:
        self.assertIsNone(constants.iso_to_epoch(None))
        self.assertIsNone(constants.iso_to_epoch(""))
        self.assertIsNone(constants.iso_to_epoch("not-a-date"))

    def test_kodi_datetime_roundtrip_through_utc(self) -> None:
        local = "2026-07-09 20:15:00"
        iso = constants.kodi_datetime_to_utc_iso(local)
        self.assertIsNotNone(iso)
        self.assertTrue(iso.endswith("Z"))
        # Converting back through the local timezone restores the original.
        self.assertEqual(constants.iso_to_kodi_datetime(iso), local)

    def test_kodi_datetime_rejects_garbage(self) -> None:
        self.assertIsNone(constants.kodi_datetime_to_utc_iso(""))
        self.assertIsNone(constants.kodi_datetime_to_utc_iso("yesterday"))
        self.assertIsNone(constants.kodi_datetime_to_epoch("2026-13-45 99:99:99"))


class ShouldApplyResumeTests(unittest.TestCase):
    def _remote(self, **overrides):
        remote = {
            "position_seconds": 1200,
            "duration_seconds": 3600,
            "updated_at": "2026-07-09T12:00:00Z",
        }
        remote.update(overrides)
        return remote

    def test_applies_to_fresh_item(self) -> None:
        kodi_item = {"resume": {"position": 0, "total": 0}, "lastplayed": ""}
        self.assertTrue(pull_sync.should_apply_resume(self._remote(), kodi_item))

    def test_skips_tiny_positions(self) -> None:
        kodi_item = {"resume": {"position": 0}, "lastplayed": ""}
        self.assertFalse(
            pull_sync.should_apply_resume(
                self._remote(position_seconds=30), kodi_item
            )
        )

    def test_skips_nearly_finished_positions(self) -> None:
        kodi_item = {"resume": {"position": 0}, "lastplayed": ""}
        self.assertFalse(
            pull_sync.should_apply_resume(
                self._remote(position_seconds=3540), kodi_item
            )
        )

    def test_skips_when_already_close(self) -> None:
        kodi_item = {"resume": {"position": 1180}, "lastplayed": ""}
        self.assertFalse(pull_sync.should_apply_resume(self._remote(), kodi_item))

    def test_skips_when_kodi_state_is_newer(self) -> None:
        # Kodi last played AFTER the remote update — local state wins.
        remote = self._remote(updated_at="2020-01-01T00:00:00Z")
        kodi_item = {
            "resume": {"position": 2400},
            "lastplayed": "2026-07-09 12:00:00",
        }
        self.assertFalse(pull_sync.should_apply_resume(remote, kodi_item))

    def test_overwrites_older_kodi_resume(self) -> None:
        # Remote update far in the future relative to Kodi's lastplayed.
        remote = self._remote(updated_at="2099-01-01T00:00:00Z")
        kodi_item = {
            "resume": {"position": 300},
            "lastplayed": "2020-01-01 12:00:00",
        }
        self.assertTrue(pull_sync.should_apply_resume(remote, kodi_item))


class LibraryIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_movies = pull_sync._get_kodi_movies
        self.original_shows = pull_sync._get_kodi_shows
        self.original_episodes = pull_sync._get_kodi_episodes
        pull_sync._get_kodi_movies = lambda: [
            {
                "movieid": 1,
                "uniqueid": {"tmdb": "550", "imdb": "tt0137523"},
                "playcount": 0,
            },
            {"movieid": 2, "uniqueid": {"imdb": "tt1375666"}, "playcount": 1},
        ]
        pull_sync._get_kodi_shows = lambda: [
            {"tvshowid": 10, "uniqueid": {"tmdb": "1396"}},
        ]
        pull_sync._get_kodi_episodes = lambda: [
            {"episodeid": 100, "tvshowid": 10, "season": 1, "episode": 2, "playcount": 0},
        ]

    def tearDown(self) -> None:
        pull_sync._get_kodi_movies = self.original_movies
        pull_sync._get_kodi_shows = self.original_shows
        pull_sync._get_kodi_episodes = self.original_episodes

    def test_movie_matching_prefers_tmdb_then_imdb(self) -> None:
        index = pull_sync.KodiLibraryIndex()

        by_tmdb = index.find_movie({"tmdb_id": 550})
        self.assertIsNotNone(by_tmdb)
        self.assertEqual(by_tmdb["movieid"], 1)

        by_imdb = index.find_movie({"tmdb_id": 27205, "imdb_id": "tt1375666"})
        self.assertIsNotNone(by_imdb)
        self.assertEqual(by_imdb["movieid"], 2)

        self.assertIsNone(index.find_movie({"tmdb_id": 99999}))

    def test_episode_matching_via_show_and_numbers(self) -> None:
        index = pull_sync.KodiLibraryIndex()

        found = index.find_episode(
            {"show_tmdb_id": 1396, "season": 1, "episode": 2}
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["episodeid"], 100)

        self.assertIsNone(
            index.find_episode({"show_tmdb_id": 1396, "season": 1, "episode": 3})
        )
        self.assertIsNone(
            index.find_episode({"show_tmdb_id": 42, "season": 1, "episode": 2})
        )


class RunPullSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_movies = pull_sync._get_kodi_movies
        self.original_shows = pull_sync._get_kodi_shows
        self.original_episodes = pull_sync._get_kodi_episodes
        self.rpc_calls: list[tuple[str, dict]] = []
        self.original_rpc = pull_sync._rpc

        pull_sync._get_kodi_movies = lambda: [
            {
                "movieid": 1,
                "uniqueid": {"tmdb": "550"},
                "playcount": 0,
                "resume": {"position": 0, "total": 0},
                "lastplayed": "",
            },
            {
                "movieid": 2,
                "uniqueid": {"tmdb": "600"},
                "playcount": 3,
                "resume": {"position": 0, "total": 0},
                "lastplayed": "2026-01-01 10:00:00",
            },
        ]
        pull_sync._get_kodi_shows = lambda: [
            {"tvshowid": 10, "uniqueid": {"tmdb": "1396"}},
        ]
        pull_sync._get_kodi_episodes = lambda: [
            {
                "episodeid": 100,
                "tvshowid": 10,
                "season": 1,
                "episode": 2,
                "playcount": 0,
                "resume": {"position": 0, "total": 0},
                "lastplayed": "",
            },
        ]

        def _capture_rpc(method: str, params: dict) -> dict:
            self.rpc_calls.append((method, params))
            return {}

        pull_sync._rpc = _capture_rpc

    def tearDown(self) -> None:
        pull_sync._get_kodi_movies = self.original_movies
        pull_sync._get_kodi_shows = self.original_shows
        pull_sync._get_kodi_episodes = self.original_episodes
        pull_sync._rpc = self.original_rpc

    def test_full_sync_marks_watched_and_sets_resume(self) -> None:
        class _FakeAPI:
            def get(self, path, timeout=0):
                _ = path, timeout
                return {
                    "movies": [
                        # Unwatched in Kodi → should be marked.
                        {"tmdb_id": 550, "watched_at": "2026-07-01T00:00:00Z", "playcount": 2},
                        # Already watched in Kodi → left alone.
                        {"tmdb_id": 600, "watched_at": "2026-07-01T00:00:00Z", "playcount": 1},
                        # Not in the library → unmatched.
                        {"tmdb_id": 999, "watched_at": "2026-07-01T00:00:00Z", "playcount": 1},
                    ],
                    "episodes": [
                        {
                            "show_tmdb_id": 1396,
                            "season": 1,
                            "episode": 2,
                            "watched_at": "2026-07-02T00:00:00Z",
                            "playcount": 1,
                        },
                    ],
                    "in_progress": [
                        {
                            "media_type": "movie",
                            "tmdb_id": 550,
                            "position_seconds": 1200,
                            "duration_seconds": 3600,
                            "updated_at": "2026-07-03T00:00:00Z",
                        },
                    ],
                }

        applied: set = set()
        summary = pull_sync.run_pull_sync(
            _FakeAPI(), apply_watched=True, apply_resume=True, applied_out=applied
        )

        self.assertEqual(summary["movies_marked"], 1)
        self.assertEqual(summary["episodes_marked"], 1)
        self.assertEqual(summary["resume_set"], 1)
        self.assertEqual(summary["unmatched"], 1)
        self.assertEqual(summary["already_synced"], 1)
        self.assertEqual(applied, {("movie", 1), ("episode", 100)})

        methods = [method for method, _ in self.rpc_calls]
        self.assertEqual(methods.count("VideoLibrary.SetMovieDetails"), 2)
        self.assertEqual(methods.count("VideoLibrary.SetEpisodeDetails"), 1)

        watched_call = next(
            params
            for method, params in self.rpc_calls
            if method == "VideoLibrary.SetMovieDetails" and "playcount" in params
        )
        self.assertEqual(watched_call["movieid"], 1)
        self.assertEqual(watched_call["playcount"], 2)
        self.assertIn("lastplayed", watched_call)

        resume_call = next(
            params
            for method, params in self.rpc_calls
            if method == "VideoLibrary.SetMovieDetails" and "resume" in params
        )
        self.assertEqual(resume_call["resume"]["position"], 1200)
        self.assertEqual(resume_call["resume"]["total"], 3600)

    def test_empty_remote_state_is_a_noop(self) -> None:
        class _FakeAPI:
            def get(self, path, timeout=0):
                _ = path, timeout
                return {"movies": [], "episodes": [], "in_progress": []}

        summary = pull_sync.run_pull_sync(
            _FakeAPI(), apply_watched=True, apply_resume=True
        )

        self.assertEqual(sum(summary.values()), 0)
        self.assertEqual(self.rpc_calls, [])

    def test_cancellation_stops_work_and_flags_summary(self) -> None:
        class _FakeAPI:
            def get(self, path, timeout=0):
                _ = path, timeout
                return {
                    "movies": [
                        {"tmdb_id": 550, "watched_at": "2026-07-01T00:00:00Z", "playcount": 1},
                        {"tmdb_id": 600, "watched_at": "2026-07-01T00:00:00Z", "playcount": 1},
                    ],
                    "episodes": [],
                    "in_progress": [],
                }

        summary = pull_sync.run_pull_sync(
            _FakeAPI(),
            apply_watched=True,
            apply_resume=True,
            progress_callback=lambda done, total: False,  # cancel immediately
        )

        self.assertEqual(summary["cancelled"], 1)
        self.assertEqual(summary["movies_marked"], 0)
        self.assertEqual(self.rpc_calls, [])

    def test_since_parameter_is_forwarded(self) -> None:
        seen_paths: list[str] = []

        class _FakeAPI:
            def get(self, path, timeout=0):
                _ = timeout
                seen_paths.append(path)
                return {"movies": [], "episodes": [], "in_progress": []}

        pull_sync.run_pull_sync(
            _FakeAPI(), apply_watched=True, apply_resume=True, since_ms=1234567890
        )

        self.assertEqual(seen_paths, ["/api/scrobble/sync?since=1234567890"])


if __name__ == "__main__":
    unittest.main()
