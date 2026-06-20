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


if __name__ == "__main__":
    unittest.main()
