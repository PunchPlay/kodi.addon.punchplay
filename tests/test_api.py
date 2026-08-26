from __future__ import annotations

import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import urllib.error
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
        getInfoLabel=lambda *args, **kwargs: "",
        Monitor=lambda: types.SimpleNamespace(
            abortRequested=lambda: False,
            waitForAbort=lambda timeout=0: False,
        ),
    )

if "xbmcgui" not in sys.modules:
    sys.modules["xbmcgui"] = types.SimpleNamespace(
        Dialog=lambda: types.SimpleNamespace(
            notification=lambda *args, **kwargs: None,
            yesno=lambda *args, **kwargs: True,
            ok=lambda *args, **kwargs: None,
        ),
        DialogProgress=lambda: types.SimpleNamespace(
            create=lambda *args, **kwargs: None,
            close=lambda: None,
            update=lambda *args, **kwargs: None,
            iscanceled=lambda: False,
        ),
        NOTIFICATION_INFO=0,
        NOTIFICATION_WARNING=1,
        NOTIFICATION_ERROR=2,
    )

if "xbmcaddon" not in sys.modules:
    sys.modules["xbmcaddon"] = types.SimpleNamespace(Addon=lambda *args, **kwargs: None)

if "xbmcvfs" not in sys.modules:
    sys.modules["xbmcvfs"] = types.SimpleNamespace(translatePath=lambda value: value)

api_module = importlib.import_module("api")


class _FakeAddon:
    def __init__(self) -> None:
        self.settings = {
            "backend_url": "",
            "developer_mode": False,
            "allow_insecure_backend_url": False,
            "scrobble_movies": True,
            "scrobble_tv": True,
            "scrobble_anime": True,
            "anime_episode_format": "auto",
            "watched_threshold": 70,
            "min_length": 5,
            "heartbeat_interval": 30,
            "rate_after_watching": True,
            "rating_prompt_delay": 2,
            "show_notifications": True,
            "notify_during_playback": False,
        }

    def getSetting(self, key: str) -> str:
        value = self.settings.get(key, "")
        return str(value)

    def getSettingBool(self, key: str) -> bool:
        return bool(self.settings.get(key, False))

    def getSettingInt(self, key: str) -> int:
        return int(self.settings.get(key, 0))

    def setSettingBool(self, key: str, value: bool) -> None:
        self.settings[key] = bool(value)

    def getAddonInfo(self, key: str) -> str:
        mapping = {
            "version": "1.3.0",
            "path": "/tmp/script.punchplay",
            "profile": "/tmp/script.punchplay/profile",
        }
        return mapping.get(key, "")

    def getLocalizedString(self, message_id: int) -> str:
        return str(message_id)


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, object]] = {}
        self.identify_results: list[tuple[str, str, float | None]] = []
        self.enqueued: list[tuple[str, dict[str, object]]] = []
        self.queue_cleared = False
        self.pending: list[dict[str, object]] = []
        self.existing_ids: set[int] = set()
        self.deleted_ids: list[int] = []
        self.expired_dropped = 0

    def get_identifier(self, key: str) -> dict[str, object] | None:
        return self.store.get(key)

    def set_identifier(self, key: str, data: dict[str, object], ttl_secs: int = 0) -> None:
        _ = ttl_secs
        self.store[key] = dict(data)

    def record_identify_result(
        self,
        *,
        status: str,
        title: str = "",
        confidence: float | None = None,
    ) -> None:
        self.identify_results.append((status, title, confidence))

    def record_error(self, error: str) -> None:
        _ = error

    def record_success(self, endpoint: str, title: str = "") -> None:
        _ = endpoint, title

    def enqueue_scrobble(self, endpoint: str, payload: dict[str, object]) -> None:
        self.enqueued.append((endpoint, payload))

    def get_queue_summary(self) -> dict[str, int]:
        return {"count": len(self.enqueued)}

    def clear_pending_scrobbles(self) -> None:
        self.enqueued = []
        self.queue_cleared = True

    def set_account_username(self, username: str | None) -> None:
        _ = username

    # -- pending-scrobble queue (flush_queue) --------------------------
    def add_pending(self, item: dict[str, object]) -> None:
        self.pending.append(item)
        self.existing_ids.add(item["id"])

    def drop_expired_pending_scrobbles(self) -> int:
        return self.expired_dropped

    def get_pending_scrobbles(self) -> list[dict[str, object]]:
        return [item for item in self.pending if item["id"] in self.existing_ids]

    def delete_pending_scrobble(self, scrobble_id: int) -> None:
        self.deleted_ids.append(scrobble_id)
        self.existing_ids.discard(scrobble_id)

    def mark_pending_scrobble_attempt(self, scrobble_id: int, error: str) -> None:
        _ = scrobble_id, error

    def pending_scrobble_exists(self, scrobble_id: int) -> bool:
        return scrobble_id in self.existing_ids


class APIValidationTests(unittest.TestCase):
    def test_validate_backend_url_accepts_https(self) -> None:
        result = api_module.validate_backend_url("https://punchplay.tv")
        self.assertTrue(result["valid"])
        self.assertEqual(result["url"], "https://punchplay.tv")

    def test_validate_backend_url_uses_default_for_blank(self) -> None:
        result = api_module.validate_backend_url("")
        self.assertTrue(result["valid"])
        self.assertTrue(result["using_default"])

    def test_validate_backend_url_rejects_javascript(self) -> None:
        result = api_module.validate_backend_url("javascript:alert(1)")
        self.assertFalse(result["valid"])

    def test_validate_backend_url_rejects_file(self) -> None:
        result = api_module.validate_backend_url("file:///tmp/test")
        self.assertFalse(result["valid"])

    def test_validate_backend_url_rejects_http_without_override(self) -> None:
        result = api_module.validate_backend_url("http://localhost:8080")
        self.assertFalse(result["valid"])

    def test_validate_backend_url_accepts_http_with_override(self) -> None:
        result = api_module.validate_backend_url(
            "http://localhost:8080",
            developer_mode=True,
            allow_insecure_http=True,
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["url"], "http://localhost:8080")


class IdentifyMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-api-tests-")
        self.fake_addon = _FakeAddon()
        self.original_get_addon = api_module.get_addon
        self.original_get_profile_dir = api_module.get_profile_dir
        self.original_get_addon_version = api_module.get_addon_version
        api_module.get_addon = lambda: self.fake_addon
        api_module.get_profile_dir = lambda: self.temp_dir
        api_module.get_addon_version = lambda: "1.3.0"
        self.cache = _FakeCache()
        self.client = api_module.APIClient(cache=self.cache)

    def tearDown(self) -> None:
        api_module.get_addon = self.original_get_addon
        api_module.get_profile_dir = self.original_get_profile_dir
        api_module.get_addon_version = self.original_get_addon_version
        shutil.rmtree(self.temp_dir)

    def test_identify_skips_backend_when_ids_exist(self) -> None:
        calls: list[tuple[str, str]] = []

        def _unexpected_request(method: str, path: str, payload=None, **kwargs):
            calls.append((method, path))
            _ = payload, kwargs
            return {}

        self.client._request = _unexpected_request  # type: ignore[method-assign]

        result = self.client.identify_media(
            {"media_type": "movie", "title": "Inception", "tmdb_id": 27205}
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_identify_applies_high_confidence_match(self) -> None:
        self.client._request = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "matched": True,
            "confidence": 0.98,
            "media_type": "movie",
            "title": "Inception",
            "year": 2010,
            "tmdb_id": 27205,
            "imdb_id": "tt1375666",
        }

        result = self.client.identify_media(
            {"media_type": "movie", "title": "Inception", "year": 2010},
            raw_filename="/Movies/Inception.2010.mkv",
            duration_seconds=8880,
        )

        self.assertEqual(result["tmdb_id"], 27205)
        self.assertEqual(result["identify_source"], "backend")
        self.assertTrue(any(value.get("matched") for value in self.cache.store.values()))

    def test_identify_rejects_low_confidence_match(self) -> None:
        self.client._request = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "matched": True,
            "confidence": 0.41,
            "media_type": "movie",
            "title": "Wrong Match",
            "tmdb_id": 1,
        }

        result = self.client.identify_media(
            {"media_type": "movie", "title": "Unknown Movie"},
            raw_filename="/Movies/Unknown.Movie.avi",
        )

        self.assertIsNone(result)
        self.assertTrue(any(value.get("matched") is False for value in self.cache.store.values()))

    def test_identify_handles_network_failure(self) -> None:
        def _raise(*args, **kwargs):
            _ = args, kwargs
            raise ConnectionError("offline")

        self.client._request = _raise  # type: ignore[method-assign]

        result = self.client.identify_media(
            {"media_type": "episode", "title": "Show", "season": 1, "episode": 2}
        )

        self.assertIsNone(result)
        self.assertEqual(self.cache.store, {})

    def test_identify_uses_cached_no_match(self) -> None:
        metadata = {"media_type": "movie", "title": "Cached Miss"}
        cache_key = self.client._identify_cache_key(  # pylint: disable=protected-access
            metadata,
            raw_filename="/Movies/Cached.Miss.mkv",
            duration_seconds=0,
        )
        self.cache.store[cache_key] = {"matched": False, "confidence": 0.0}

        calls: list[str] = []

        def _unexpected_request(*args, **kwargs):
            calls.append("called")
            _ = args, kwargs
            return {}

        self.client._request = _unexpected_request  # type: ignore[method-assign]
        result = self.client.identify_media(
            metadata,
            raw_filename="/Movies/Cached.Miss.mkv",
        )
        self.assertIsNone(result)
        self.assertEqual(calls, [])


class TokenRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-refresh-tests-")
        self.fake_addon = _FakeAddon()
        self.original_get_addon = api_module.get_addon
        self.original_get_profile_dir = api_module.get_profile_dir
        self.original_get_addon_version = api_module.get_addon_version
        self.original_localize = api_module.localize
        api_module.get_addon = lambda: self.fake_addon
        api_module.get_profile_dir = lambda: self.temp_dir
        api_module.get_addon_version = lambda: "1.5.2"
        api_module.localize = lambda message_id: str(message_id)
        self.client = api_module.APIClient()
        self.client._tokens = {
            "access_token": "expired-access",
            "refresh_token": "refresh-1",
        }

    def tearDown(self) -> None:
        api_module.get_addon = self.original_get_addon
        api_module.get_profile_dir = self.original_get_profile_dir
        api_module.get_addon_version = self.original_get_addon_version
        api_module.localize = self.original_localize
        shutil.rmtree(self.temp_dir)

    def test_in_flight_401_reuses_token_rotated_by_another_thread(self) -> None:
        """A delayed 401 must not rotate the refresh chain a second time."""

        class _Response:
            def __init__(self, payload: dict[str, str]) -> None:
                self._raw = json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

            def read(self) -> bytes:
                return self._raw

        first_request_started = threading.Event()
        first_refresh_completed = threading.Event()
        state_lock = threading.Lock()
        expired_request_count = 0
        refresh_tokens: list[str] = []

        def _unauthorized(url: str) -> urllib.error.HTTPError:
            return urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                {},
                io.BytesIO(b'{"message":"Unauthorized"}'),
            )

        def _urlopen(request, timeout=0):
            nonlocal expired_request_count
            _ = timeout
            if request.full_url.endswith("/api/auth/refresh"):
                payload = json.loads(request.data.decode("utf-8"))
                with state_lock:
                    refresh_tokens.append(payload["refresh_token"])
                first_refresh_completed.set()
                return _Response(
                    {"access_token": "access-2", "refresh_token": "refresh-2"}
                )

            authorization = request.headers.get("Authorization")
            if authorization == "Bearer expired-access":
                with state_lock:
                    expired_request_count += 1
                    request_number = expired_request_count
                if request_number == 1:
                    first_request_started.set()
                    if not first_refresh_completed.wait(2):
                        raise RuntimeError("test refresh did not complete")
                raise _unauthorized(request.full_url)

            if authorization == "Bearer access-2":
                return _Response({"ok": "true"})

            raise AssertionError(f"unexpected authorization: {authorization!r}")

        original_urlopen = api_module.urllib.request.urlopen
        self.addCleanup(
            setattr,
            api_module.urllib.request,
            "urlopen",
            original_urlopen,
        )
        api_module.urllib.request.urlopen = _urlopen
        results: list[dict[str, str]] = []
        errors: list[BaseException] = []

        def _request() -> None:
            try:
                results.append(self.client.get("/api/test"))
            except BaseException as exc:  # keep worker failures visible below
                errors.append(exc)

        delayed = threading.Thread(target=_request)
        delayed.start()
        self.assertTrue(first_request_started.wait(2))

        refreshing = threading.Thread(target=_request)
        refreshing.start()
        delayed.join(timeout=3)
        refreshing.join(timeout=3)

        self.assertFalse(delayed.is_alive())
        self.assertFalse(refreshing.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(refresh_tokens, ["refresh-1"])

    def test_refresh_request_sends_the_same_user_agent_as_every_other_call(self) -> None:
        # The refresh call used to build its own bare headers dict, skipping
        # the User-Agent every other request sends. A default urllib
        # User-Agent is a common bot-mitigation signature at the network
        # edge — this call was getting rejected with a 403 before ever
        # reaching the backend route, in production, on every single attempt.
        class _Response:
            def __init__(self, payload: dict[str, str]) -> None:
                self._raw = json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

            def read(self) -> bytes:
                return self._raw

        captured_headers: dict[str, str] = {}

        def _urlopen(request, timeout=0):
            _ = timeout
            captured_headers.update(request.headers)
            return _Response({"access_token": "access-2", "refresh_token": "refresh-2"})

        original_urlopen = api_module.urllib.request.urlopen
        self.addCleanup(
            setattr, api_module.urllib.request, "urlopen", original_urlopen
        )
        api_module.urllib.request.urlopen = _urlopen

        refreshed = self.client._do_refresh("expired-access", self.client._auth_generation)

        self.assertTrue(refreshed)
        # urllib.Request lower-cases none of this; header keys arrive
        # capitalized-first (e.g. "User-agent") via Request.headers.
        user_agent = next(
            (v for k, v in captured_headers.items() if k.lower() == "user-agent"),
            None,
        )
        self.assertIsNotNone(user_agent)
        self.assertIn("script.punchplay", user_agent)
        self.assertNotIn("python-urllib", (user_agent or "").lower())


class LogoutGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-logout-tests-")
        self.fake_addon = _FakeAddon()
        self.original_get_addon = api_module.get_addon
        self.original_get_profile_dir = api_module.get_profile_dir
        self.original_get_addon_version = api_module.get_addon_version
        self.original_localize = api_module.localize
        api_module.get_addon = lambda: self.fake_addon
        api_module.get_profile_dir = lambda: self.temp_dir
        api_module.get_addon_version = lambda: "1.5.2"
        api_module.localize = lambda message_id: str(message_id)
        self.cache = _FakeCache()
        self.client = api_module.APIClient(cache=self.cache)
        self.client._tokens = {  # pylint: disable=protected-access
            "access_token": "old-account-access",
            "refresh_token": "old-account-refresh",
        }

    def tearDown(self) -> None:
        api_module.get_addon = self.original_get_addon
        api_module.get_profile_dir = self.original_get_profile_dir
        api_module.get_addon_version = self.original_get_addon_version
        api_module.localize = self.original_localize
        shutil.rmtree(self.temp_dir)

    def test_logout_prevents_in_flight_post_from_repopulating_queue(self) -> None:
        request_started = threading.Event()
        release_request = threading.Event()

        def _slow_failure(*args, **kwargs):
            _ = args, kwargs
            request_started.set()
            release_request.wait(2)
            raise ConnectionError("offline")

        self.client._request = _slow_failure  # type: ignore[method-assign]
        result: list[dict[str, str] | None] = []
        worker = threading.Thread(
            target=lambda: result.append(
                self.client.post("/api/scrobble/stop", {"event_id": "old-event"})
            )
        )
        worker.start()
        self.assertTrue(request_started.wait(1))

        self.assertTrue(self.client.logout())
        self.assertEqual(self.client.auth_generation, 1)
        release_request.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertTrue(self.cache.queue_cleared)
        self.assertEqual(self.cache.enqueued, [])

    def test_logout_prevents_queue_full_stop_from_repopulating_queue(self) -> None:
        originating_generation = self.client.auth_generation

        self.assertTrue(self.client.logout())

        self.assertFalse(
            self.client.preserve_post_for_retry(
                "/api/scrobble/stop",
                {"event_id": "old-event"},
                expected_auth_generation=originating_generation,
            )
        )
        self.assertTrue(self.cache.queue_cleared)
        self.assertEqual(self.cache.enqueued, [])

    def test_delayed_401_cannot_retry_old_post_with_new_account_token(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                _ = args

            def read(self) -> bytes:
                return b'{"ok":true}'

        old_request_started = threading.Event()
        release_old_request = threading.Event()
        authorizations: list[str | None] = []

        def _urlopen(request, timeout=0):
            _ = timeout
            authorization = request.headers.get("Authorization")
            authorizations.append(authorization)
            if authorization == "Bearer old-account-access":
                old_request_started.set()
                release_old_request.wait(2)
                raise urllib.error.HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    io.BytesIO(b'{"message":"Unauthorized"}'),
                )
            if authorization == "Bearer new-account-access":
                return _Response()
            raise AssertionError(f"unexpected authorization: {authorization!r}")

        original_urlopen = api_module.urllib.request.urlopen
        self.addCleanup(
            setattr,
            api_module.urllib.request,
            "urlopen",
            original_urlopen,
        )
        api_module.urllib.request.urlopen = _urlopen

        result: list[dict[str, str] | None] = []
        worker = threading.Thread(
            target=lambda: result.append(
                self.client.post("/api/scrobble/stop", {"event_id": "old-event"})
            )
        )
        worker.start()
        self.assertTrue(old_request_started.wait(1))

        self.assertTrue(self.client.logout())
        self.client._save_tokens(  # pylint: disable=protected-access
            {
                "access_token": "new-account-access",
                "refresh_token": "new-account-refresh",
            }
        )
        release_old_request.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        self.assertEqual(authorizations, ["Bearer old-account-access"])
        self.assertEqual(self.cache.enqueued, [])


class FlushQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="punchplay-api-tests-")
        self.fake_addon = _FakeAddon()
        self.original_get_addon = api_module.get_addon
        self.original_get_profile_dir = api_module.get_profile_dir
        self.original_get_addon_version = api_module.get_addon_version
        api_module.get_addon = lambda: self.fake_addon
        api_module.get_profile_dir = lambda: self.temp_dir
        api_module.get_addon_version = lambda: "1.3.0"
        self.cache = _FakeCache()
        self.client = api_module.APIClient(cache=self.cache)

    def tearDown(self) -> None:
        api_module.get_addon = self.original_get_addon
        api_module.get_profile_dir = self.original_get_profile_dir
        api_module.get_addon_version = self.original_get_addon_version
        shutil.rmtree(self.temp_dir)

    def test_flush_replays_a_row_that_still_exists(self) -> None:
        self.cache.add_pending(
            {"id": 1, "endpoint": "/api/scrobble/progress", "payload": {"event_id": "a"}}
        )
        requests_made: list[str] = []
        self.client._request = lambda method, path, payload=None, **kwargs: (  # type: ignore[method-assign]
            requests_made.append(path) or {}
        )

        completed = self.client.flush_queue()

        self.assertTrue(completed)
        self.assertEqual(requests_made, ["/api/scrobble/progress"])
        self.assertEqual(self.cache.deleted_ids, [1])

    def test_flush_skips_a_row_deleted_out_from_under_it(self) -> None:
        # Reproduces the race a PR review comment flagged: the service's
        # periodic flush_queue() snapshots pending rows via
        # get_pending_scrobbles(), then a concurrent onPlayBackEnded's
        # _send_stop() calls delete_pending_scrobbles_for_session() for
        # the same item before flush_queue gets to it. Replaying it
        # anyway would resend a stale progress event to the backend after
        # the authoritative stop already landed.
        self.cache.add_pending(
            {"id": 1, "endpoint": "/api/scrobble/progress", "payload": {"event_id": "stale"}}
        )
        # It existed in the snapshot, then disappeared before the replay
        # loop's live-row check.
        self.cache.pending_scrobble_exists = lambda scrobble_id: False

        requests_made: list[str] = []
        self.client._request = lambda method, path, payload=None, **kwargs: (  # type: ignore[method-assign]
            requests_made.append(path) or {}
        )

        completed = self.client.flush_queue()

        self.assertTrue(completed)
        self.assertEqual(requests_made, [])
        self.assertEqual(self.cache.deleted_ids, [])

    def test_flush_stops_when_account_changes_mid_flush(self) -> None:
        # pending_scrobbles rows carry no per-row account identity, so a
        # flush spanning a logout+relogin to a different account must not
        # send a later row under whatever account is active by the time the
        # loop reaches it — it belongs to the generation the flush started
        # with. _request() raises AuthenticationChangedError when the
        # account no longer matches expected_auth_generation; the loop must
        # stop there rather than continuing to the next row.
        self.cache.add_pending(
            {"id": 1, "endpoint": "/api/scrobble/progress", "payload": {"event_id": "a"}}
        )
        self.cache.add_pending(
            {"id": 2, "endpoint": "/api/scrobble/progress", "payload": {"event_id": "b"}}
        )
        requests_made: list[str] = []

        def _fake_request(method, path, payload=None, **kwargs):
            requests_made.append(path)
            if len(requests_made) == 2:
                raise api_module.AuthenticationChangedError("account changed")
            return {}

        self.client._request = _fake_request  # type: ignore[method-assign]

        completed = self.client.flush_queue(expected_auth_generation=0)

        self.assertFalse(completed)
        self.assertEqual(len(requests_made), 2)
        self.assertEqual(self.cache.deleted_ids, [1])

    def test_flush_reports_incomplete_after_a_transient_failure(self) -> None:
        self.cache.add_pending(
            {"id": 1, "endpoint": "/api/scrobble/stop", "payload": {"event_id": "a"}}
        )
        self.client._request = (  # type: ignore[method-assign]
            lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("offline"))
        )

        completed = self.client.flush_queue()

        self.assertFalse(completed)
        self.assertEqual(self.cache.deleted_ids, [])


if __name__ == "__main__":
    unittest.main()
