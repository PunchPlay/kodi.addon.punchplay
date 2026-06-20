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

    def post(self, *args, **kwargs):
        _ = args, kwargs
        return {}

    def post_immediate(self, *args, **kwargs):
        _ = args, kwargs
        return {}

    def flush_queue(self) -> None:
        return None

    def is_authenticated(self) -> bool:
        return True


class _FakeCache:
    def has_rating_suppression(self, key: str) -> bool:
        _ = key
        return False

    def set_rating_suppression(self, key: str, scope: str) -> None:
        _ = key, scope

    def delete_pending_scrobbles_for_session(self, playback_session_id: str) -> None:
        _ = playback_session_id


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
        keys = player_module.build_rating_suppression_keys(
            {
                "media_type": "episode",
                "title": "Breaking Bad",
                "season": 1,
                "episode": 2,
                "tmdb_id": 1396,
            }
        )
        self.assertIn("title", keys)
        self.assertIn("show", keys)
        self.assertIn("1396", keys["title"])

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


if __name__ == "__main__":
    unittest.main()
