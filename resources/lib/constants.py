"""Shared addon constants and lightweight helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import xbmcaddon
import xbmcvfs

ADDON_ID = "script.punchplay"
ADDON_NAME = "PunchPlay"
ADDON_DISPLAY_NAME = "PunchPlay"
NOTIFICATION_TITLE = ADDON_NAME
DEFAULT_BACKEND_URL = "https://punchplay.tv"

SCROBBLE_START_ENDPOINT = "/api/scrobble/start"
SCROBBLE_PROGRESS_ENDPOINT = "/api/scrobble/progress"
SCROBBLE_PAUSE_ENDPOINT = "/api/scrobble/pause"
SCROBBLE_RESUME_ENDPOINT = "/api/scrobble/resume"
SCROBBLE_STOP_ENDPOINT = "/api/scrobble/stop"
SCROBBLE_RATE_ENDPOINT = "/api/scrobble/rate"
SCROBBLE_IMPORT_ENDPOINT = "/api/scrobble/import"
SCROBBLE_SYNC_ENDPOINT = "/api/scrobble/sync"

AUTH_DEVICE_CODE_ENDPOINT = "/api/auth/device/code"
AUTH_DEVICE_TOKEN_ENDPOINT = "/api/auth/device/token"
AUTH_REFRESH_ENDPOINT = "/api/auth/refresh"
AUTH_ME_ENDPOINT = "/api/auth/me"
IDENTIFY_ENDPOINT = "/api/identify"

ACTION_PROPERTY_LOGIN = "punchplay_login"
ACTION_PROPERTY_LOGOUT = "punchplay_logout"
ACTION_PROPERTY_SYNC_LIBRARY = "punchplay_sync_library"
ACTION_PROPERTY_PREVIEW_LIBRARY = "punchplay_preview_library"
ACTION_PROPERTY_TEST_CONNECTION = "punchplay_test_connection"
ACTION_PROPERTY_SHOW_STATUS = "punchplay_show_status"
ACTION_PROPERTY_EXPORT_DEBUG = "punchplay_export_debug"
ACTION_PROPERTY_EXPORT_VERBOSE_DEBUG = "punchplay_export_verbose_debug"
ACTION_PROPERTY_CLEAR_QUEUE = "punchplay_clear_queue"
ACTION_PROPERTY_PULL_SYNC = "punchplay_pull_sync"
ACTION_PROPERTY_CLEAR_SUPPRESSIONS = "punchplay_clear_suppressions"

HOME_WINDOW_ID = 10000

FLUSH_INTERVAL_SECS = 60
PRUNE_INTERVAL_SECS = 24 * 60 * 60
IDENTIFIER_CACHE_TTL_SECS = 7 * 24 * 60 * 60
IDENTIFIER_SUCCESS_CACHE_TTL_SECS = 30 * 24 * 60 * 60
IDENTIFIER_NO_MATCH_CACHE_TTL_SECS = 24 * 60 * 60
QUEUE_ENTRY_MAX_AGE_SECS = 30 * 24 * 60 * 60
OFFLINE_QUEUE_MAX_ITEMS = 500
# Flush touches at most one failing entry per minute, so this cap (~1 day of
# continuous failures) drops poison payloads the backend will never accept.
MAX_QUEUE_ATTEMPTS = 1440
LIBRARY_SYNC_BATCH_SIZE = 50
STOP_COMPLETE_GRACE_SECS = 3
HEARTBEAT_INTERVAL_SECS = 15
HEARTBEAT_MAX_CONSECUTIVE_ERRORS = 3

PULL_SYNC_INTERVAL_SECS = 6 * 60 * 60
PULL_SYNC_OVERLAP_SECS = 60 * 60
PULL_SYNC_TIMEOUT_SECS = 60
AUTO_PULL_CHECK_INTERVAL_SECS = 60

# Live watched-state sync (VideoLibrary.OnUpdate → PunchPlay import).
LIVE_SYNC_DEBOUNCE_SECS = 2.0
LIVE_SYNC_MAX_BATCH = 100
# How long after playback a library item's playcount bump is treated as an
# echo of our own scrobble rather than a manual toggle.
LIVE_SYNC_RECENT_PLAY_WINDOW_SECS = 600
# How long pull-sync-applied items stay suppressed from live sync.
LIVE_SYNC_PULL_APPLIED_SUPPRESS_SECS = 600
# Library scan finished → wait for Kodi to settle, then pull sync at most
# once per SCAN_SYNC_MIN_INTERVAL_SECS.
SCAN_SYNC_DELAY_SECS = 60
SCAN_SYNC_MIN_INTERVAL_SECS = 30 * 60

REQUEST_TIMEOUT_SECS = 15
TEST_CONNECTION_TIMEOUT_SECS = 5
IDENTIFY_REQUEST_TIMEOUT_SECS = 5
IDENTIFY_MATCH_THRESHOLD = 0.85

PERMANENT_HTTP_STATUS_CODES = (400, 403, 404, 422)


def get_addon() -> xbmcaddon.Addon:
    return xbmcaddon.Addon(ADDON_ID)


def get_addon_path() -> str:
    return get_addon().getAddonInfo("path")


def get_addon_version() -> str:
    return get_addon().getAddonInfo("version")


def get_profile_dir() -> str:
    return xbmcvfs.translatePath(get_addon().getAddonInfo("profile"))


def localize(message_id: int) -> str:
    return get_addon().getLocalizedString(message_id)


def mask_value(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return value
    return "{0}…{1}".format(value[:visible], value[-visible:])


def kodi_datetime_to_utc_iso(value: str | None) -> str | None:
    """Convert Kodi's local-time "YYYY-MM-DD HH:MM:SS" to a UTC ISO 8601 string."""
    if not value:
        return None
    try:
        parsed = time.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        epoch = time.mktime(parsed)  # interprets the struct as local time
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    except (ValueError, OverflowError):
        return None


def kodi_datetime_to_epoch(value: str | None) -> float | None:
    """Convert Kodi's local-time "YYYY-MM-DD HH:MM:SS" to a Unix timestamp."""
    if not value:
        return None
    try:
        return time.mktime(time.strptime(value.strip(), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def iso_to_epoch(value: str | None) -> float | None:
    """Parse an ISO 8601 timestamp (with Z or offset) to a Unix timestamp."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def iso_to_kodi_datetime(value: str | None) -> str | None:
    """Convert an ISO 8601 timestamp to Kodi's local-time "YYYY-MM-DD HH:MM:SS"."""
    epoch = iso_to_epoch(value)
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))
