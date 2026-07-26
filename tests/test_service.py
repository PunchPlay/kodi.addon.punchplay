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


if __name__ == "__main__":
    unittest.main()
