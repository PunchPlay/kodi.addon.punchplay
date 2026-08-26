from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest

LIB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "lib")
)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

class _Monitor:
    """Stand-in for xbmc.Monitor.

    service.PunchPlayService subclasses this, so it has to be a real class —
    the other test modules register a lambda, which cannot be subclassed.
    The instance methods keep those modules working after we overwrite it.
    """

    def abortRequested(self) -> bool:
        return False

    def waitForAbort(self, timeout: float = 0) -> bool:
        return False


if "xbmc" not in sys.modules:
    sys.modules["xbmc"] = types.SimpleNamespace(
        LOGDEBUG=0,
        LOGINFO=1,
        LOGWARNING=2,
        log=lambda *args, **kwargs: None,
        executeJSONRPC=lambda payload: "{}",
    )

sys.modules["xbmc"].Monitor = _Monitor

if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = types.SimpleNamespace(
        NOTIFICATION_INFO=0,
        NOTIFICATION_WARNING=1,
        NOTIFICATION_ERROR=2,
        Dialog=lambda: types.SimpleNamespace(
            notification=lambda *args, **kwargs: None,
            yesno=lambda *args, **kwargs: False,
            textviewer=lambda *args, **kwargs: None,
        ),
        DialogProgress=lambda: types.SimpleNamespace(
            create=lambda *args, **kwargs: None,
            close=lambda: None,
            update=lambda *args, **kwargs: None,
            iscanceled=lambda: False,
        ),
        Window=lambda window_id: types.SimpleNamespace(
            getProperty=lambda key: "",
            clearProperty=lambda key: None,
        ),
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
service = importlib.import_module("service")


class _Progress:
    """Minimal DialogProgress stand-in."""

    def __init__(self, cancel_after: int | None = None) -> None:
        self.cancel_after = cancel_after
        self.checks = 0

    def iscanceled(self) -> bool:
        self.checks += 1
        if self.cancel_after is None:
            return False
        return self.checks > self.cancel_after

    def update(self, *args, **kwargs) -> None:
        return None


class _Api:
    """Records posts and replays a scripted sequence of outcomes."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def post_immediate(self, endpoint, payload, timeout=30):
        self.calls += 1
        outcome = (
            self.outcomes.pop(0)
            if self.outcomes
            else {"imported": len(payload["entries"])}
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_service(api) -> service.PunchPlayService:
    # Bypass __init__ — it builds a Cache, APIClient and Player.
    svc = service.PunchPlayService.__new__(service.PunchPlayService)
    svc._api = api
    return svc


def _fresh_totals() -> dict[str, int]:
    return {
        "imported": 0,
        "skipped_duplicates": 0,
        "unmatched": 0,
        "rejected_items": 0,
        "failed_batches": 0,
        "not_sent": 0,
    }


def _entries(count: int) -> list[dict[str, object]]:
    return [{"media_type": "movie", "title": f"Movie {i}"} for i in range(count)]


class PostLibraryBatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        # Other test modules overwrite the shared xbmcaddon mock, so pin the
        # string lookup to something deterministic for the duration of a test
        # rather than depending on module import order.
        self._real_localize = service.localize
        # "{0}" tolerates any number of format arguments; a wider template
        # would break on the single-argument strings.
        service.localize = lambda message_id: "{0}"

    def tearDown(self) -> None:
        service.localize = self._real_localize

    def _run(self, entries, api, progress=None, dry_run=False):
        totals = _fresh_totals()
        diagnostics: list[dict[str, object]] = []
        outcome = _make_service(api)._post_library_batches(
            entries,
            endpoint="/api/scrobble/import",
            dry_run=dry_run,
            progress=progress or _Progress(),
            progress_base=0,
            progress_span=50,
            message_id=32025,
            totals=totals,
            diagnostics=diagnostics,
        )
        return outcome, totals, diagnostics

    def test_batches_are_capped_at_the_backend_maximum(self) -> None:
        # The backend rejects >100 entries per batch with a permanent 400.
        self.assertLessEqual(constants.LIBRARY_SYNC_BATCH_SIZE, 100)

    def test_full_run_accumulates_totals(self) -> None:
        api = _Api(
            [
                {"imported": 100, "skipped_duplicates": 3, "unmatched": 1},
                {"imported": 50, "skipped_duplicates": 0, "unmatched": 2},
            ]
        )
        outcome, totals, _ = self._run(_entries(150), api)

        self.assertEqual(outcome, "done")
        self.assertEqual(api.calls, 2)
        self.assertEqual(totals["imported"], 150)
        self.assertEqual(totals["skipped_duplicates"], 3)
        self.assertEqual(totals["unmatched"], 3)
        self.assertEqual(totals["not_sent"], 0)

    def test_isolated_failure_is_counted_but_run_continues(self) -> None:
        api = _Api(
            [
                RuntimeError("boom"),
                {"imported": 100},
                {"imported": 100},
            ]
        )
        outcome, totals, _ = self._run(_entries(300), api)

        self.assertEqual(outcome, "done")
        self.assertEqual(api.calls, 3)
        self.assertEqual(totals["imported"], 200)
        # The failed batch's entries are reported, not silently dropped.
        self.assertEqual(totals["failed_batches"], 1)
        self.assertEqual(totals["not_sent"], 100)

    def test_repeated_failures_abort_and_bank_the_remainder(self) -> None:
        api = _Api([RuntimeError("down")] * 10)
        outcome, totals, _ = self._run(_entries(1000), api)

        self.assertEqual(outcome, "failed")
        # Stops after the configured streak instead of grinding through all 10.
        self.assertEqual(api.calls, constants.LIBRARY_SYNC_MAX_CONSECUTIVE_FAILURES)
        self.assertEqual(
            totals["failed_batches"], constants.LIBRARY_SYNC_MAX_CONSECUTIVE_FAILURES
        )
        # Every entry is accounted for: attempted-and-failed plus never-tried.
        self.assertEqual(totals["not_sent"], 1000)
        self.assertEqual(totals["imported"], 0)

    def test_failure_streak_resets_after_a_success(self) -> None:
        api = _Api(
            [
                RuntimeError("blip"),
                RuntimeError("blip"),
                {"imported": 100},
                RuntimeError("blip"),
                RuntimeError("blip"),
                {"imported": 100},
            ]
        )
        outcome, totals, _ = self._run(_entries(600), api)

        self.assertEqual(outcome, "done")
        self.assertEqual(api.calls, 6)
        self.assertEqual(totals["failed_batches"], 4)
        self.assertEqual(totals["not_sent"], 400)

    def test_cancellation_stops_immediately(self) -> None:
        api = _Api([{"imported": 100}] * 10)
        outcome, totals, _ = self._run(
            _entries(1000), api, progress=_Progress(cancel_after=2)
        )

        self.assertEqual(outcome, "cancelled")
        self.assertEqual(api.calls, 2)
        self.assertEqual(totals["imported"], 200)

    def test_dry_run_raises_on_the_first_failure(self) -> None:
        api = _Api([RuntimeError("preview exploded")])
        with self.assertRaises(RuntimeError):
            self._run(_entries(100), api, dry_run=True)

    def test_diagnostics_are_collected_across_batches(self) -> None:
        api = _Api(
            [
                {"imported": 100, "items": [{"title": "A"}]},
                {"imported": 100, "items": [{"title": "B"}]},
            ]
        )
        _, _, diagnostics = self._run(_entries(200), api)

        self.assertEqual([item["title"] for item in diagnostics], ["A", "B"])


class LiveWatchedRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library_events = importlib.import_module("library_events")
        self.original_build_import_entry = self.library_events.build_import_entry
        self.original_get_addon = service.get_addon
        service.get_addon = lambda: types.SimpleNamespace(
            getSettingBool=lambda key: True
        )

    def tearDown(self) -> None:
        self.library_events.build_import_entry = self.original_build_import_entry
        service.get_addon = self.original_get_addon

    def test_transient_detail_failure_requeues_original_toggle(self) -> None:
        event = {"item_type": "movie", "library_id": 42, "playcount": 1}
        requeued: list[dict[str, object]] = []

        self.library_events.build_import_entry = lambda item: (_ for _ in ()).throw(
            self.library_events.LibraryDetailLookupError("temporary failure")
        )
        svc = service.PunchPlayService.__new__(service.PunchPlayService)
        svc._api = types.SimpleNamespace(
            is_authenticated=lambda: True,
            post=lambda *args, **kwargs: self.fail("post should not run"),
        )
        svc._player = types.SimpleNamespace(recent_library_items=lambda: [])
        svc._live_sync = types.SimpleNamespace(
            pending_count=lambda: 1,
            pop_due_events=lambda recent: [event],
            requeue_events=lambda events: requeued.extend(events),
        )

        svc._push_watched_toggles()

        self.assertEqual(requeued, [event])

    def _push_anime_movie(
        self,
        *,
        movies_enabled: bool,
        anime_enabled: bool,
    ) -> list[dict[str, object]]:
        event = {"item_type": "movie", "library_id": 42, "playcount": 1}
        self.library_events.build_import_entry = lambda item: {
            "media_type": "movie",
            "title": "Spirited Away",
            "tmdb_id": 129,
            "anime": True,
        }
        settings = {
            "scrobble_movies": movies_enabled,
            "scrobble_tv": True,
            "scrobble_anime": anime_enabled,
        }
        service.get_addon = lambda: types.SimpleNamespace(
            getSettingBool=lambda key: settings[key]
        )
        posted: list[dict[str, object]] = []
        svc = service.PunchPlayService.__new__(service.PunchPlayService)
        svc._api = types.SimpleNamespace(is_authenticated=lambda: True)
        svc._player = types.SimpleNamespace(
            recent_library_items=lambda: [],
            dispatch_import=lambda entries: posted.append({"entries": entries}),
        )
        svc._live_sync = types.SimpleNamespace(
            pending_count=lambda: 1,
            pop_due_events=lambda recent: [event],
            requeue_events=lambda events: None,
        )

        svc._push_watched_toggles()
        return posted

    def test_anime_movie_is_skipped_when_movie_scrobbling_is_disabled(self) -> None:
        posted = self._push_anime_movie(
            movies_enabled=False,
            anime_enabled=True,
        )

        self.assertEqual(posted, [])

    def test_anime_movie_ignores_disabled_anime_episode_setting(self) -> None:
        posted = self._push_anime_movie(
            movies_enabled=True,
            anime_enabled=False,
        )

        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["entries"][0]["title"], "Spirited Away")


class _PullSyncApi:
    def is_authenticated(self) -> bool:
        return True


class _PullSyncAddon:
    def getSettingBool(self, key: str) -> bool:
        return key in ("pull_watched", "pull_resume", "show_notifications")


def _pull_sync_summary(**overrides) -> dict[str, int]:
    base = {
        "movies_marked": 0,
        "episodes_marked": 0,
        "resume_set": 0,
        "unmatched": 0,
        "already_synced": 0,
        "cancelled": 0,
        "apply_failed": 0,
    }
    base.update(overrides)
    return base


class PullSyncCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-pull-sync-tests-")
        self.cache_module = importlib.import_module("cache")
        self.original_get_profile_dir = self.cache_module.get_profile_dir
        self.cache_module.get_profile_dir = lambda: self.temp_dir
        self.cache = self.cache_module.Cache()

        self.pull_sync_module = importlib.import_module("pull_sync")
        self.original_run_pull_sync = self.pull_sync_module.run_pull_sync

        self.original_get_addon = service.get_addon
        service.get_addon = lambda: _PullSyncAddon()
        self.original_localize = service.localize
        service.localize = lambda message_id: "{0} {1}"

        # Don't rely on the shared sys.modules["xbmcgui"] mock — test_player.py
        # unconditionally replaces it without a "DialogProgress" attribute,
        # and module import order across test files determines which mock
        # service.py ends up bound to. Swap in a self-contained fake instead.
        self.notifications: list[tuple] = []
        self.original_xbmcgui = service.xbmcgui
        service.xbmcgui = types.SimpleNamespace(
            NOTIFICATION_INFO="info",
            NOTIFICATION_WARNING="warning",
            NOTIFICATION_ERROR="error",
            Dialog=lambda: types.SimpleNamespace(
                notification=lambda title, message, icon, timeout=5000: (
                    self.notifications.append((message, icon))
                ),
                yesno=lambda *args, **kwargs: False,
            ),
            DialogProgress=lambda: types.SimpleNamespace(
                create=lambda *args, **kwargs: None,
                close=lambda: None,
                update=lambda *args, **kwargs: None,
                iscanceled=lambda: False,
            ),
        )

    def tearDown(self) -> None:
        self.pull_sync_module.run_pull_sync = self.original_run_pull_sync
        service.get_addon = self.original_get_addon
        service.localize = self.original_localize
        service.xbmcgui = self.original_xbmcgui
        self.cache_module.get_profile_dir = self.original_get_profile_dir
        shutil.rmtree(self.temp_dir)

    def _svc(self) -> service.PunchPlayService:
        svc = service.PunchPlayService.__new__(service.PunchPlayService)
        svc._api = _PullSyncApi()
        svc._cache = self.cache
        svc._live_sync = types.SimpleNamespace(record_pull_applied=lambda applied: None)
        return svc

    def test_apply_failed_holds_checkpoint_then_advances_after_max_held_runs(self) -> None:
        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: _pull_sync_summary(apply_failed=1)
        )
        svc = self._svc()
        max_held = constants.PULL_SYNC_MAX_HELD_RUNS

        for _ in range(max_held - 1):
            svc._pull_sync(manual=False)
            self.assertIsNone(self.cache.get_runtime_status()["last_pull_sync_at"])

        # The run that hits the cap advances the checkpoint anyway, rather
        # than blocking every future sync behind one permanently-bad item.
        svc._pull_sync(manual=False)
        status = self.cache.get_runtime_status()
        self.assertIsNotNone(status["last_pull_sync_at"])
        self.assertEqual(status["pull_sync_held_runs"], 0)

    def test_successful_run_resets_held_counter(self) -> None:
        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: _pull_sync_summary(apply_failed=1)
        )
        svc = self._svc()
        svc._pull_sync(manual=False)
        self.assertEqual(self.cache.get_runtime_status()["pull_sync_held_runs"], 1)

        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: _pull_sync_summary(movies_marked=1)
        )
        svc._pull_sync(manual=False)

        status = self.cache.get_runtime_status()
        self.assertIsNotNone(status["last_pull_sync_at"])
        self.assertEqual(status["pull_sync_held_runs"], 0)

    def test_full_pull_failure_clears_an_older_incremental_checkpoint(self) -> None:
        self.cache.record_pull_sync("previous incremental run")

        def _run(*args, **kwargs):
            _ = args
            kwargs["failed_out"].add("movie-old")
            return _pull_sync_summary(apply_failed=1)

        self.pull_sync_module.run_pull_sync = _run

        self._svc()._pull_sync(manual=True)

        status = self.cache.get_runtime_status()
        self.assertIsNone(status["last_pull_sync_at"])
        self.assertEqual(status["pull_sync_held_runs"], 1)

    def test_full_pull_exception_clears_an_older_incremental_checkpoint(self) -> None:
        self.cache.record_pull_sync("previous incremental run")
        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        )

        self._svc()._pull_sync(manual=False)

        self.assertIsNone(self.cache.get_runtime_status()["last_pull_sync_at"])

    def test_new_failed_item_gets_its_own_retry_allowance(self) -> None:
        failures = [
            {"movie-a"},
            {"movie-a"},
            {"movie-a", "movie-b"},
            {"movie-a", "movie-b"},
            {"movie-a", "movie-b"},
        ]

        def _run(*args, **kwargs):
            _ = args
            current = failures.pop(0)
            kwargs["failed_out"].update(current)
            return _pull_sync_summary(apply_failed=len(current))

        self.pull_sync_module.run_pull_sync = _run
        svc = self._svc()

        # movie-a has failed three times here, but movie-b has only failed
        # once, so advancing would silently spend movie-b's retry allowance.
        for _ in range(3):
            svc._pull_sync(manual=False)
        self.assertIsNone(self.cache.get_runtime_status()["last_pull_sync_at"])
        self.assertEqual(self.cache.get_runtime_status()["pull_sync_held_runs"], 1)

        svc._pull_sync(manual=False)
        self.assertIsNone(self.cache.get_runtime_status()["last_pull_sync_at"])

        svc._pull_sync(manual=False)
        self.assertIsNotNone(self.cache.get_runtime_status()["last_pull_sync_at"])

    def test_manual_sync_notification_reflects_apply_failed(self) -> None:
        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: _pull_sync_summary(apply_failed=2)
        )
        svc = self._svc()

        svc._pull_sync(manual=True)

        self.assertEqual(len(self.notifications), 1)
        message, icon = self.notifications[0]
        self.assertEqual(icon, service.xbmcgui.NOTIFICATION_WARNING)
        self.assertNotIn("already up to date", message.lower())

    def test_manual_sync_notification_is_success_without_failures(self) -> None:
        self.pull_sync_module.run_pull_sync = (
            lambda *args, **kwargs: _pull_sync_summary(movies_marked=1)
        )
        svc = self._svc()

        svc._pull_sync(manual=True)

        self.assertEqual(len(self.notifications), 1)
        _, icon = self.notifications[0]
        self.assertEqual(icon, service.xbmcgui.NOTIFICATION_INFO)


if __name__ == "__main__":
    unittest.main()
