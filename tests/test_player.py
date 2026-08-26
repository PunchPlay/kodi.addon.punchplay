from __future__ import annotations

import importlib
import os
import queue
import sys
import threading
import time
import types
import unittest

LIB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "resources", "lib")
)
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)


class _FakePlayerBase:
    def isPlayingVideo(self) -> bool:
        return False

    def getTime(self) -> float:
        return 0.0

    def getTotalTime(self) -> float:
        return 0.0


sys.modules["xbmc"] = types.SimpleNamespace(
    Player=_FakePlayerBase,
    Monitor=lambda: types.SimpleNamespace(
        abortRequested=lambda: False,
        waitForAbort=lambda timeout=0: False,
    ),
    LOGDEBUG=0,
    LOGINFO=1,
    LOGWARNING=2,
    log=lambda *args, **kwargs: None,
)

sys.modules["xbmcgui"] = types.SimpleNamespace(
    Dialog=lambda: types.SimpleNamespace(
        notification=lambda *args, **kwargs: None,
        select=lambda *args, **kwargs: -1,
    ),
    NOTIFICATION_INFO=0,
)

sys.modules["xbmcaddon"] = types.SimpleNamespace(Addon=lambda *args, **kwargs: None)
sys.modules["xbmcvfs"] = types.SimpleNamespace(translatePath=lambda value: value)

player_module = importlib.import_module("player")


class _FakeAddon:
    def getSettingInt(self, key: str) -> int:
        defaults = {
            "watched_threshold": 70,
            "min_length": 5,
            "heartbeat_interval": 30,
            "rating_prompt_delay": 2,
        }
        return defaults.get(key, 0)

    def getSettingBool(self, key: str) -> bool:
        defaults = {
            "scrobble_movies": True,
            "scrobble_tv": True,
            "scrobble_anime": True,
            "show_notifications": True,
            "notify_during_playback": False,
            "rate_after_watching": True,
        }
        return defaults.get(key, False)

    def getSetting(self, key: str) -> str:
        if key == "anime_episode_format":
            return "auto"
        return ""

    def setSettingBool(self, key: str, value: bool) -> None:
        _ = key, value

    def getAddonInfo(self, key: str) -> str:
        mapping = {"path": "/tmp/script.punchplay", "version": "1.3.0"}
        return mapping.get(key, "")

    def getLocalizedString(self, message_id: int) -> str:
        return str(message_id)


class _FakeAPI:
    device_id = "device-1234"
    auth_generation = 0

    def post(self, *args, **kwargs):
        _ = args, kwargs
        return {}

    def post_immediate(self, *args, **kwargs):
        _ = args, kwargs
        return {}

    def flush_queue(self, *, expected_auth_generation: int | None = None) -> bool:
        _ = expected_auth_generation
        return True

    def preserve_post_for_retry(
        self,
        endpoint,
        payload,
        *,
        expected_auth_generation,
    ) -> bool:
        _ = endpoint, payload, expected_auth_generation
        return True

    def is_authenticated(self) -> bool:
        return True


class _FakeCache:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict]] = []
        self.deleted_sessions: list[str] = []

    def has_rating_suppression(self, key: str) -> bool:
        _ = key
        return False

    def set_rating_suppression(self, key: str, scope: str) -> None:
        _ = key, scope

    def delete_pending_scrobbles_for_session(self, playback_session_id: str) -> None:
        self.deleted_sessions.append(playback_session_id)

    def enqueue_scrobble(self, endpoint: str, payload: dict) -> None:
        self.enqueued.append((endpoint, payload))

    def enqueue_scrobbles(self, items: list[tuple[str, dict]]) -> None:
        self.enqueued.extend(items)


class PlayerHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_get_addon = player_module.get_addon
        self.original_get_addon_version = player_module.get_addon_version
        player_module.get_addon = lambda: _FakeAddon()
        player_module.get_addon_version = lambda: "1.3.0"

    def tearDown(self) -> None:
        player_module.get_addon = self.original_get_addon
        player_module.get_addon_version = self.original_get_addon_version

    def test_rating_suppression_keys_are_stable(self) -> None:
        # Kodi reports a distinct uniqueid per episode (InfoTagVideo's
        # getUniqueIDs() is episode-scoped, not show-scoped), so two
        # episodes of the same show realistically carry different
        # tmdb_id values. The "show" key must still match across them —
        # only "title" is allowed to vary per episode.
        keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "year": 2008,
                "season": 1,
                "episode": 2,
                "tmdb_id": 62161,
            }
        )
        next_episode_keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "year": 2008,
                "season": 1,
                "episode": 3,
                "tmdb_id": 62163,
            }
        )
        self.assertIn("title", keys)
        self.assertIn("show", keys)
        self.assertIn("62161", keys["title"])
        self.assertNotEqual(keys["title"], next_episode_keys["title"])
        self.assertEqual(keys["show"], next_episode_keys["show"])
        self.assertNotIn("62161", keys["show"])

    def test_show_suppression_key_survives_a_year_and_id_change_across_seasons(
        self,
    ) -> None:
        # `year` is InfoTagVideo.getYear() scraped per episode, and
        # tmdb_id/tvdb_id/imdb_id are per-episode too — a show whose
        # seasons aired in different calendar years, with different
        # per-episode ids, must not silently stop matching "Never for
        # this show".
        season_one_keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "year": 2008,
                "season": 1,
                "episode": 2,
                "tmdb_id": 62161,
            }
        )
        season_five_keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "year": 2013,
                "season": 5,
                "episode": 14,
                "tmdb_id": 62311,
            }
        )
        self.assertEqual(season_one_keys["show"], season_five_keys["show"])

    def test_show_suppression_key_ignores_missing_ids_too(self) -> None:
        # Without any canonical id at all, the key still has to be built
        # from something stable — title, never year.
        season_one_keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Unidentified Show",
                "year": 2008,
                "season": 1,
                "episode": 1,
            }
        )
        season_two_keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Unidentified Show",
                "year": 2009,
                "season": 2,
                "episode": 1,
            }
        )
        self.assertEqual(season_one_keys["show"], season_two_keys["show"])

    def test_payload_includes_event_identity_fields(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        player._playback_session_id = "session-1"  # pylint: disable=protected-access

        payload = player._build_payload(  # pylint: disable=protected-access
            {"media_type": "movie", "title": "Inception", "year": 2010},
            position=120.0,
            duration=240.0,
        )

        self.assertIn("event_id", payload)
        self.assertEqual(payload["playback_session_id"], "session-1")
        self.assertIn("event_created_at", payload)
        self.assertEqual(payload["client_version"], "1.3.0")

    def test_rating_prompt_queued_and_popped_when_due(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        metadata = {"media_type": "movie", "title": "Inception", "tmdb_id": 27205}

        player._queue_rating_prompt(  # pylint: disable=protected-access
            metadata, {"rating_prompt_delay": 0}, stop_resp={}
        )
        request = player.pop_due_rating_prompt()

        self.assertIsNotNone(request)
        self.assertEqual(request["metadata"]["title"], "Inception")
        self.assertIn("title", request["suppression_keys"])
        # Popping consumed the request.
        self.assertIsNone(player.pop_due_rating_prompt())

    def test_rating_prompt_not_due_before_delay(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        player._queue_rating_prompt(  # pylint: disable=protected-access
            {"media_type": "movie", "title": "Inception", "tmdb_id": 27205},
            {"rating_prompt_delay": 60},
            stop_resp={},
        )

        self.assertIsNone(player.pop_due_rating_prompt())
        # Still pending — not consumed by the early poll.
        self.assertIsNotNone(player._pending_rating)  # pylint: disable=protected-access

    def test_rating_prompt_dropped_when_video_playing(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        player.isPlayingVideo = lambda: True  # type: ignore[method-assign]
        player._queue_rating_prompt(  # pylint: disable=protected-access
            {"media_type": "movie", "title": "Inception", "tmdb_id": 27205},
            {"rating_prompt_delay": 0},
            stop_resp={},
        )

        self.assertIsNone(player.pop_due_rating_prompt())
        # Dropped, not deferred — autoplay cancels the prompt.
        self.assertIsNone(player._pending_rating)  # pylint: disable=protected-access

    def test_rating_prompt_skipped_without_reliable_identity(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        player._queue_rating_prompt(  # pylint: disable=protected-access
            {"media_type": "movie", "title": "Unknown Movie"},
            {"rating_prompt_delay": 0},
            stop_resp=None,
        )

        self.assertIsNone(player._pending_rating)  # pylint: disable=protected-access

    def test_movies_only_scope_skips_episode_rating_prompt(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())

        player._queue_rating_prompt(  # pylint: disable=protected-access
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "season": 1,
                "episode": 2,
                "tmdb_id": 62085,
            },
            {"rating_prompt_delay": 0, "rating_prompt_scope": "movies"},
            stop_resp={},
        )

        self.assertIsNone(player._pending_rating)  # pylint: disable=protected-access

    def test_movies_only_scope_still_queues_movie_rating_prompt(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())

        player._queue_rating_prompt(  # pylint: disable=protected-access
            {
                "media_type": "movie",
                "title": "Inception",
                "tmdb_id": 27205,
            },
            {"rating_prompt_delay": 0, "rating_prompt_scope": "movies"},
            stop_resp={},
        )

        self.assertIsNotNone(player._pending_rating)  # pylint: disable=protected-access

    def test_duplicate_stop_guard_emits_stop_once(self) -> None:
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        calls: list[str] = []

        def _record_stop(settings) -> None:
            _ = settings
            calls.append("stop")

        player._emit_stop = _record_stop  # type: ignore[method-assign]  # pylint: disable=protected-access
        player._metadata = {"media_type": "movie", "title": "Inception"}  # pylint: disable=protected-access
        player._handle_stop()  # pylint: disable=protected-access
        player._handle_stop()  # pylint: disable=protected-access

        self.assertEqual(calls, ["stop"])

    def test_untracked_stop_still_refreshes_echo_suppression(self) -> None:
        # An untracked play (identify() failed, or below min_length_minutes)
        # never sets _metadata, but onAVStarted still remembers the library
        # item so its playcount echo can be suppressed. _handle_stop must
        # still refresh that stamp even though nothing was tracked, or the
        # suppression window can lapse before Kodi's own echo arrives.
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=_FakeCache())
        calls: list[tuple[str, int]] = []
        player._stamp_library_item = (  # type: ignore[method-assign]  # pylint: disable=protected-access
            lambda media_type, dbid: calls.append((media_type, dbid))
        )
        player._current_library_item = ("movie", 42)  # pylint: disable=protected-access

        player._handle_stop()  # pylint: disable=protected-access

        self.assertEqual(calls, [("movie", 42)])
        self.assertIsNone(player._current_library_item)  # pylint: disable=protected-access


class _RecordingAPI(_FakeAPI):
    """Records posts, and can be made to block mid-post."""

    def __init__(self, block: threading.Event | None = None) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.preserved: list[tuple[str, dict]] = []
        self.block = block
        self.entered = threading.Event()
        self.stop_response: dict = {}
        self.flush_succeeds = True
        self.flush_error: Exception | None = None

    def post(self, endpoint, payload, *, expected_auth_generation=None):
        _ = expected_auth_generation
        self.entered.set()
        if self.block is not None:
            self.block.wait(5)
        self.posts.append((endpoint, payload))
        return dict(self.stop_response)

    def flush_queue(self, *, expected_auth_generation: int | None = None) -> bool:
        _ = expected_auth_generation
        self.posts.append(("__flush__", {}))
        if self.flush_error is not None:
            raise self.flush_error
        return self.flush_succeeds

    def preserve_post_for_retry(
        self,
        endpoint,
        payload,
        *,
        expected_auth_generation,
    ) -> bool:
        _ = expected_auth_generation
        self.preserved.append((endpoint, payload))
        return True


def _settings(**overrides):
    base = {
        "watched_threshold": 0.7,
        "rate_after_watching": True,
        "show_notifications": False,
        "notify_during_playback": False,
        "rating_prompt_delay": 0,
    }
    base.update(overrides)
    return base


class PostWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_get_addon = player_module.get_addon
        self.original_get_addon_version = player_module.get_addon_version
        player_module.get_addon = lambda: _FakeAddon()
        player_module.get_addon_version = lambda: "1.3.0"
        self.players: list = []

    def tearDown(self) -> None:
        for player in self.players:
            player.cleanup()
        player_module.get_addon = self.original_get_addon
        player_module.get_addon_version = self.original_get_addon_version

    def _player(self, api):
        player = player_module.PunchPlayPlayer(api=api, cache=_FakeCache())
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access
        self.players.append(player)
        return player

    def _drain(self, player) -> None:
        player._post_queue.join()  # pylint: disable=protected-access

    def test_dispatch_does_not_block_the_calling_thread(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)

        started = time.monotonic()
        player._dispatch_post("/api/scrobble/start", {"title": "Inception"})
        elapsed = time.monotonic() - started

        # The worker is stuck inside post(); the caller must have returned.
        self.assertTrue(api.entered.wait(5))
        self.assertLess(elapsed, 1.0)
        self.assertEqual(api.posts, [])

        block.set()
        self._drain(player)
        self.assertEqual(len(api.posts), 1)

    def test_events_are_posted_in_order(self) -> None:
        api = _RecordingAPI()
        player = self._player(api)

        for endpoint in ("start", "progress", "pause", "resume", "stop"):
            player._dispatch_post(f"/api/scrobble/{endpoint}", {"n": endpoint})
        self._drain(player)

        self.assertEqual(
            [endpoint for endpoint, _ in api.posts],
            [
                "/api/scrobble/start",
                "/api/scrobble/progress",
                "/api/scrobble/pause",
                "/api/scrobble/resume",
                "/api/scrobble/stop",
            ],
        )

    def test_dispatch_import_runs_on_the_worker_not_the_caller(self) -> None:
        # _push_watched_toggles() used to call api.post() directly on the
        # service loop's own thread; dispatch_import() routes it through
        # the same post worker as every other network write instead.
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)
        entries = [{"title": "Spirited Away"}]

        started = time.monotonic()
        player.dispatch_import(entries)
        elapsed = time.monotonic() - started

        self.assertTrue(api.entered.wait(5))
        self.assertLess(elapsed, 1.0)

        block.set()
        self._drain(player)
        self.assertEqual(api.posts, [(player_module.SCROBBLE_IMPORT_ENDPOINT, {"entries": entries})])

    def test_dispatch_import_preserves_entries_when_queue_is_full(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        preserved: list[tuple[str, dict]] = []
        api.preserve_post_for_retry = (  # type: ignore[attr-defined]
            lambda endpoint, payload, expected_auth_generation: (
                preserved.append((endpoint, payload)) or True
            )
        )
        player = self._player(api)
        player._post_queue = queue.Queue(maxsize=1)  # pylint: disable=protected-access
        original_timeout = player_module.POST_QUEUE_PUT_TIMEOUT_SECS
        player_module.POST_QUEUE_PUT_TIMEOUT_SECS = 0.01
        try:
            # Occupies the worker (blocked inside post()); the queue's one
            # remaining slot is the only space left.
            player._dispatch_post("/api/scrobble/progress", {"n": 0})
            self.assertTrue(api.entered.wait(1))
            player._dispatch_post("/api/scrobble/progress", {"n": 1})

            entries = [{"title": "Spirited Away"}]
            player.dispatch_import(entries)
        finally:
            block.set()
            self._drain(player)
            player_module.POST_QUEUE_PUT_TIMEOUT_SECS = original_timeout

        self.assertEqual(
            preserved,
            [(player_module.SCROBBLE_IMPORT_ENDPOINT, {"entries": entries})],
        )

    def test_start_drains_offline_queue_before_dispatching(self) -> None:
        # _dispatch_start bundles the flush and the start into a single
        # worker job's run() — a stale event left over from a session that
        # ended offline is always flushed before the new start reaches the
        # network, or it could be delivered after the start and clobber the
        # now-playing state the start just set.
        api = _RecordingAPI()
        player = self._player(api)

        player._dispatch_start({"title": "Inception"})
        self._drain(player)

        self.assertEqual(
            [endpoint for endpoint, _ in api.posts],
            ["__flush__", player_module.SCROBBLE_START_ENDPOINT],
        )

    def test_start_waits_when_offline_queue_drain_is_incomplete(self) -> None:
        api = _RecordingAPI()
        api.flush_succeeds = False
        player = self._player(api)
        payload = {
            "title": "Inception",
            "playback_session_id": "session-1",
            "event_created_at": 2000,
        }

        player._dispatch_start(payload)
        self._drain(player)

        self.assertEqual(api.posts, [("__flush__", {})])
        self.assertEqual(
            api.preserved,
            [(player_module.SCROBBLE_START_ENDPOINT, payload)],
        )

    def test_start_is_preserved_when_offline_queue_drain_raises(self) -> None:
        api = _RecordingAPI()
        api.flush_error = RuntimeError("sqlite unavailable")
        player = self._player(api)
        payload = {
            "playback_session_id": "session-1",
            "event_created_at": 2000,
        }

        player._dispatch_start(payload)
        self._drain(player)

        self.assertEqual(api.posts, [("__flush__", {})])
        self.assertEqual(
            api.preserved,
            [(player_module.SCROBBLE_START_ENDPOINT, payload)],
        )

    def test_later_session_events_stay_queued_behind_a_deferred_start(self) -> None:
        api = _RecordingAPI()
        api.flush_succeeds = False
        player = self._player(api)
        start = {
            "playback_session_id": "session-1",
            "event_created_at": 2000,
        }
        progress = {
            "playback_session_id": "session-1",
            "event_created_at": 3000,
        }

        player._dispatch_start(start)
        player._dispatch_post(player_module.SCROBBLE_PROGRESS_ENDPOINT, progress)
        self._drain(player)

        self.assertEqual(api.posts, [("__flush__", {})])
        self.assertEqual(
            api.preserved,
            [
                (player_module.SCROBBLE_START_ENDPOINT, start),
                (player_module.SCROBBLE_PROGRESS_ENDPOINT, progress),
            ],
        )

    def test_start_flush_and_post_cannot_be_split_by_a_full_queue(self) -> None:
        # Two separate dispatch calls could be split by a full queue: the
        # flush drops (only STOP jobs get preserved-for-retry) while a slot
        # frees up in time for the start to still get through — recreating
        # the exact race dispatch_flush() exists to prevent, under the load
        # conditions where it matters most. Bundling them into one _PostJob
        # means a full queue can only reject the pair together.
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)
        player._post_queue = queue.Queue(maxsize=1)  # pylint: disable=protected-access
        original_timeout = player_module.POST_QUEUE_PUT_TIMEOUT_SECS
        player_module.POST_QUEUE_PUT_TIMEOUT_SECS = 0.01
        try:
            # Occupies the worker (blocked inside post()); the queue's one
            # remaining slot is the only space left.
            player._dispatch_post("/api/scrobble/progress", {"n": 0})
            self.assertTrue(api.entered.wait(1))
            player._dispatch_post("/api/scrobble/progress", {"n": 1})

            # No room left for the combined job — it must be preserved whole.
            payload = {
                "title": "Inception",
                "playback_session_id": "session-1",
            }
            player._dispatch_start(payload)
        finally:
            block.set()
            self._drain(player)
            player_module.POST_QUEUE_PUT_TIMEOUT_SECS = original_timeout

        endpoints = [endpoint for endpoint, _ in api.posts]
        self.assertNotIn(player_module.SCROBBLE_START_ENDPOINT, endpoints)
        self.assertNotIn("__flush__", endpoints)
        self.assertEqual(
            api.preserved,
            [(player_module.SCROBBLE_START_ENDPOINT, payload)],
        )

    def test_concurrent_flushes_never_run_at_the_same_time(self) -> None:
        # The service loop's periodic timer and a pre-start drain both call
        # dispatch_flush() now — never APIClient.flush_queue() directly on
        # their own thread — specifically so two flush attempts can't race
        # each other's check/request/delete sequence. Both funnelling
        # through the same worker means they simply queue up.
        api = _RecordingAPI()
        player = self._player(api)

        player.dispatch_flush()
        player.dispatch_flush()
        player._dispatch_post(player_module.SCROBBLE_START_ENDPOINT, {"title": "Inception"})
        self._drain(player)

        self.assertEqual(
            [endpoint for endpoint, _ in api.posts],
            ["__flush__", "__flush__", player_module.SCROBBLE_START_ENDPOINT],
        )

    def test_emit_stop_returns_before_the_network_call_finishes(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)
        player._metadata = {"media_type": "movie", "title": "Inception"}
        player._playback_session_id = "session-1"

        started = time.monotonic()
        player._emit_stop(_settings())
        elapsed = time.monotonic() - started

        self.assertTrue(api.entered.wait(5))
        self.assertLess(elapsed, 1.0)

        block.set()
        self._drain(player)
        self.assertEqual(api.posts[0][0], player_module.SCROBBLE_STOP_ENDPOINT)

    def test_send_stop_merges_canonical_ids_into_the_rating_prompt(self) -> None:
        api = _RecordingAPI()
        api.stop_response = {"tmdb_id": 27205, "punchplay_id": "pp-1"}
        player = self._player(api)

        player._send_stop(
            payload={"title": "Inception"},
            metadata={"media_type": "movie", "title": "Inception"},
            settings=_settings(),
            session_id="session-1",
            auth_generation=api.auth_generation,
            watched=True,
        )

        request = player.pop_due_rating_prompt()
        self.assertIsNotNone(request)
        self.assertEqual(request["metadata"]["tmdb_id"], 27205)
        self.assertEqual(request["metadata"]["punchplay_id"], "pp-1")

    def test_send_stop_skips_rating_when_not_watched(self) -> None:
        api = _RecordingAPI()
        player = self._player(api)

        player._send_stop(
            payload={"title": "Inception"},
            metadata={"media_type": "movie", "title": "Inception", "tmdb_id": 1},
            settings=_settings(),
            session_id=None,
            auth_generation=api.auth_generation,
            watched=False,
        )

        self.assertIsNone(player._pending_rating)

    def test_full_queue_drops_instead_of_blocking(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)
        # Shrink the queue so the test does not need 100 events.
        player._post_queue = queue.Queue(maxsize=1)

        # One event occupies the worker, one fills the queue, the third has
        # nowhere to go and must be dropped rather than stall the caller.
        for i in range(3):
            player._dispatch_post("/api/scrobble/progress", {"n": i})

        self.assertTrue(api.entered.wait(5))
        block.set()
        self._drain(player)
        self.assertLessEqual(len(api.posts), 2)

    def test_full_queue_persists_authoritative_stop(self) -> None:
        # Goes through the real production path (_emit_stop, not the
        # generic _dispatch_post) so this also proves the offline_fallback
        # still runs _send_stop's session cleanup, not just the persist —
        # a queue-full STOP used to bypass _send_stop entirely and skip it.
        block = threading.Event()
        api = _RecordingAPI(block=block)
        cache = _FakeCache()
        api.preserve_post_for_retry = (  # type: ignore[attr-defined]
            lambda endpoint, payload, expected_auth_generation: (
                cache.enqueue_scrobble(endpoint, payload) or True
            )
        )
        player = player_module.PunchPlayPlayer(api=api, cache=cache)
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access
        player._metadata = {"media_type": "movie", "title": "Inception"}  # pylint: disable=protected-access
        player._playback_session_id = "session-1"  # pylint: disable=protected-access
        player._post_queue = queue.Queue(maxsize=1)  # pylint: disable=protected-access
        self.players.append(player)
        original_timeout = player_module.POST_QUEUE_PUT_TIMEOUT_SECS
        player_module.POST_QUEUE_PUT_TIMEOUT_SECS = 0.01
        try:
            player._dispatch_post(
                "/api/scrobble/progress",
                {"event_id": "active", "event_created_at": 1000},
            )
            self.assertTrue(api.entered.wait(1))
            player._dispatch_post(
                "/api/scrobble/progress",
                {"event_id": "queued", "event_created_at": 2000},
            )

            # _handle_stop (not _emit_stop directly) so _stop_emitted/
            # _metadata are cleared properly — otherwise tearDown's cleanup()
            # sees _metadata still set and fires a second, real stop dispatch.
            player._handle_stop()

            self.assertEqual(len(cache.enqueued), 1)
            self.assertEqual(cache.enqueued[0][0], player_module.SCROBBLE_STOP_ENDPOINT)
            self.assertEqual(cache.deleted_sessions, ["session-1"])
        finally:
            block.set()
            self._drain(player)
            player_module.POST_QUEUE_PUT_TIMEOUT_SECS = original_timeout

    def test_logout_discards_posts_queued_for_the_previous_account(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        player = self._player(api)

        player._playback_auth_generation = 0  # pylint: disable=protected-access
        player._dispatch_post("/api/scrobble/progress", {"event_id": "active"})
        self.assertTrue(api.entered.wait(1))
        player._dispatch_post("/api/scrobble/stop", {"event_id": "queued"})

        api.auth_generation = 1
        player.handle_logout()

        self.assertEqual(player._post_queue.qsize(), 0)  # pylint: disable=protected-access
        block.set()
        self._drain(player)
        self.assertEqual(
            [payload["event_id"] for _, payload in api.posts],
            ["active"],
        )

    def test_logout_discards_rating_from_an_in_flight_stop(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        api.stop_response = {"tmdb_id": 27205}
        player = self._player(api)
        player._metadata = {  # pylint: disable=protected-access
            "media_type": "movie",
            "title": "Inception",
            "tmdb_id": 27205,
        }
        player._playback_session_id = "session-1"  # pylint: disable=protected-access

        player._emit_stop(_settings())  # pylint: disable=protected-access
        self.assertTrue(api.entered.wait(1))
        api.auth_generation += 1
        player.handle_logout()
        block.set()
        self._drain(player)

        self.assertIsNone(player._pending_rating)  # pylint: disable=protected-access

    def test_cleanup_drains_queued_posts(self) -> None:
        api = _RecordingAPI()
        player = player_module.PunchPlayPlayer(api=api, cache=_FakeCache())
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access

        player._dispatch_post("/api/scrobble/stop", {"title": "Inception"})
        player.cleanup()

        # cleanup() joins the worker, so the stop event is already sent.
        self.assertEqual(len(api.posts), 1)

    def test_cleanup_emits_stop_for_playback_still_active_at_shutdown(self) -> None:
        # Kodi quitting mid-playback never fires onPlayBackStopped or
        # onPlayBackEnded — those only cover playback ending while Kodi keeps
        # running. cleanup() is the only signal a whole-app quit gives, so it
        # must go through _handle_stop and actually emit the stop event, not
        # just clear local state — otherwise the item is left "now playing"
        # on the backend forever.
        api = _RecordingAPI()
        player = player_module.PunchPlayPlayer(api=api, cache=_FakeCache())
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access
        player._metadata = {"media_type": "movie", "title": "Inception"}  # pylint: disable=protected-access
        player._playback_session_id = "session-1"  # pylint: disable=protected-access

        player.cleanup()

        self.assertEqual(len(api.posts), 1)
        self.assertEqual(api.posts[0][0], player_module.SCROBBLE_STOP_ENDPOINT)
        self.assertIsNone(player._metadata)  # pylint: disable=protected-access

    def test_persist_unsent_queue_drains_to_offline_cache(self) -> None:
        # Jobs still sitting in the queue when shutdown gives up on the
        # worker must not just vanish with the abandoned daemon thread.
        cache = _FakeCache()
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=cache)
        player._post_queue.put(  # pylint: disable=protected-access
            player_module._PostJob("/api/scrobble/progress", {"n": 1}, 0, lambda: None)
        )
        player._post_queue.put(  # pylint: disable=protected-access
            player_module._PostJob("/api/scrobble/stop", {"n": 2}, 0, lambda: None)
        )

        player._persist_unsent_queue()  # pylint: disable=protected-access

        self.assertEqual(
            cache.enqueued,
            [("/api/scrobble/progress", {"n": 1}), ("/api/scrobble/stop", {"n": 2})],
        )
        self.assertEqual(player._post_queue.qsize(), 0)  # pylint: disable=protected-access

    def test_persist_unsent_queue_skips_the_flush_sentinel(self) -> None:
        # A queue-drain job (see dispatch_flush) has no real endpoint or
        # payload to replay — persisting it verbatim would create a bogus
        # pending_scrobbles row that later gets POSTed to a fake path.
        cache = _FakeCache()
        player = player_module.PunchPlayPlayer(api=_FakeAPI(), cache=cache)
        player._post_queue.put(  # pylint: disable=protected-access
            player_module._PostJob(
                player_module._FLUSH_QUEUE_SENTINEL, {}, 0, lambda: None
            )
        )
        player._post_queue.put(  # pylint: disable=protected-access
            player_module._PostJob("/api/scrobble/stop", {"n": 1}, 0, lambda: None)
        )

        player._persist_unsent_queue()  # pylint: disable=protected-access

        self.assertEqual(cache.enqueued, [("/api/scrobble/stop", {"n": 1})])

    def test_cleanup_persists_the_in_flight_job_after_timeout(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        cache = _FakeCache()
        player = player_module.PunchPlayPlayer(api=api, cache=cache)
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access
        original_timeout = player_module.POST_WORKER_JOIN_TIMEOUT_SECS
        player_module.POST_WORKER_JOIN_TIMEOUT_SECS = 0.01
        worker = None
        try:
            payload = {"event_id": "stop-1", "event_created_at": 1000}
            player._dispatch_post("/api/scrobble/stop", payload)
            self.assertTrue(api.entered.wait(1))
            worker = player._post_thread  # pylint: disable=protected-access

            player.cleanup()

            self.assertIn(("/api/scrobble/stop", payload), cache.enqueued)
        finally:
            block.set()
            if worker is not None:
                worker.join(timeout=1)
            player_module.POST_WORKER_JOIN_TIMEOUT_SECS = original_timeout

    def test_cleanup_persists_backlog_when_shutdown_sentinel_queue_is_full(self) -> None:
        block = threading.Event()
        api = _RecordingAPI(block=block)
        cache = _FakeCache()
        player = player_module.PunchPlayPlayer(api=api, cache=cache)
        player._playback_auth_generation = api.auth_generation  # pylint: disable=protected-access
        player._post_queue = queue.Queue(maxsize=1)  # pylint: disable=protected-access
        original_timeout = player_module.POST_WORKER_JOIN_TIMEOUT_SECS
        player_module.POST_WORKER_JOIN_TIMEOUT_SECS = 0.01
        worker = None
        try:
            active = {"event_id": "progress-1", "event_created_at": 1000}
            queued = {"event_id": "stop-1", "event_created_at": 2000}
            player._dispatch_post("/api/scrobble/progress", active)
            self.assertTrue(api.entered.wait(1))
            player._dispatch_post("/api/scrobble/stop", queued)
            worker = player._post_thread  # pylint: disable=protected-access

            player.cleanup()

            persisted_ids = {payload["event_id"] for _, payload in cache.enqueued}
            self.assertEqual(persisted_ids, {"progress-1", "stop-1"})
        finally:
            block.set()
            if worker is not None:
                worker.join(timeout=1)
            player_module.POST_WORKER_JOIN_TIMEOUT_SECS = original_timeout


if __name__ == "__main__":
    unittest.main()
