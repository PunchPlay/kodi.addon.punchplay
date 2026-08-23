"""
library_events.py — live watched-state sync (Kodi library → PunchPlay).

Kodi announces `VideoLibrary.OnUpdate` whenever a library item changes; when
the change is a watched toggle the payload carries a `playcount` field.  The
service's onNotification callback pushes those into LiveWatchedSync (queueing
only — it runs on Kodi's announce thread), and the service loop drains due
events into batched /api/scrobble/import posts.

Echo suppression — playcount bumps that are *not* manual user toggles:
  • Our own pull sync setting playcounts   → record_pull_applied()
  • Kodi bumping playcount after playback  → recently-played dbid guard
  • Library scans (`added`/`transaction`)  → dropped at parse time
  • Un-watching (playcount 0)              → ignored; never deletes history
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import xbmc

from constants import (
    LIVE_SYNC_DEBOUNCE_SECS,
    LIVE_SYNC_MAX_BATCH,
    LIVE_SYNC_ECHO_SUPPRESS_SECS,
    kodi_datetime_to_utc_iso,
)
from pull_sync import _rpc


def parse_video_library_update(data: str | None) -> dict[str, Any] | None:
    """
    Parse a VideoLibrary.OnUpdate payload into a watched-toggle event, or
    None when the update is not a manual watched change.
    """
    if not data:
        return None
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    # Scan-driven updates are not user toggles.
    if payload.get("added") or payload.get("transaction"):
        return None

    playcount = payload.get("playcount")
    if not isinstance(playcount, int) or playcount <= 0:
        # Missing playcount = metadata-only update; 0 = un-watch (ignored —
        # we never delete PunchPlay history from a Kodi toggle).
        return None

    # Kodi versions differ: item fields nested under "item" or at top level.
    item = payload.get("item")
    if not isinstance(item, dict):
        item = payload
    item_type = item.get("type")
    library_id = item.get("id")
    if item_type not in ("movie", "episode") or not isinstance(library_id, int):
        return None

    return {
        "item_type": item_type,
        "library_id": library_id,
        "playcount": playcount,
    }


class LiveWatchedSync:
    """Thread-safe queue of watched-toggle events with echo suppression."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        # (item_type, library_id) → monotonic time the pull sync touched it.
        self._pull_applied: dict[tuple[str, int], float] = {}

    def record_pull_applied(self, applied: set[tuple[str, int]]) -> None:
        now = time.monotonic()
        with self._lock:
            for key in applied:
                self._pull_applied[key] = now

    def push_update(self, data: str | None) -> None:
        """Queue a raw OnUpdate payload.  Safe from Kodi's announce thread."""
        event = parse_video_library_update(data)
        if event is None:
            return
        event["at"] = time.monotonic()
        key = (event["item_type"], event["library_id"])
        with self._lock:
            # Coalesce repeated toggles of the same item — keep the latest.
            self._events = [
                existing
                for existing in self._events
                if (existing["item_type"], existing["library_id"]) != key
            ]
            self._events.append(event)
        xbmc.log(
            "[PunchPlay] Watched toggle queued: {0} #{1} playcount={2}".format(
                event["item_type"], event["library_id"], event["playcount"]
            ),
            xbmc.LOGDEBUG,
        )

    def pending_count(self) -> int:
        with self._lock:
            return len(self._events)

    def pop_due_events(
        self,
        recent_library_items: list[tuple[str, int, float]] | None = None,
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return events older than the debounce window (so bulk "mark season
        watched" operations batch up), dropping suppressed echoes.

        `recent_library_items` is the player's list of (item_type, dbid,
        monotonic_time) for items recently played — Kodi bumps their
        playcount at stop, which our stop scrobble already covered.
        """
        current = time.monotonic() if now is None else now
        with self._lock:
            due = [
                event
                for event in self._events
                if current - event["at"] >= LIVE_SYNC_DEBOUNCE_SECS
            ]
            if not due:
                return []
            due = due[:LIVE_SYNC_MAX_BATCH]
            taken = {(event["item_type"], event["library_id"]) for event in due}
            self._events = [
                event
                for event in self._events
                if (event["item_type"], event["library_id"]) not in taken
            ]

            # Prune stale pull-applied suppressions while we hold the lock.
            cutoff = current - LIVE_SYNC_ECHO_SUPPRESS_SECS
            self._pull_applied = {
                key: applied_at
                for key, applied_at in self._pull_applied.items()
                if applied_at >= cutoff
            }
            pull_applied = dict(self._pull_applied)

        recent = recent_library_items or []
        result: list[dict[str, Any]] = []
        for event in due:
            key = (event["item_type"], event["library_id"])
            if key in pull_applied:
                xbmc.log(
                    f"[PunchPlay] Watched toggle {key} suppressed (pull sync echo)",
                    xbmc.LOGDEBUG,
                )
                continue
            if any(
                item_type == event["item_type"] and library_id == event["library_id"]
                for item_type, library_id, _ in recent
            ):
                xbmc.log(
                    f"[PunchPlay] Watched toggle {key} suppressed (recently played)",
                    xbmc.LOGDEBUG,
                )
                continue
            result.append(event)
        return result

    def clear(self) -> None:
        with self._lock:
            self._events = []


# ----------------------------------------------------------------------
# Import-entry builders (single items, mirrors the bulk library import)
# ----------------------------------------------------------------------


def _extract_ids(details: dict[str, Any]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    unique_ids = details.get("uniqueid") or {}
    imdb = unique_ids.get("imdb") or details.get("imdbnumber") or None
    tmdb = unique_ids.get("tmdb")
    if imdb:
        ids["imdb_id"] = imdb
    if tmdb:
        try:
            ids["tmdb_id"] = int(tmdb)
        except (ValueError, TypeError):
            pass
    return ids


def build_movie_import_entry(
    library_id: int,
    playcount: int,
) -> dict[str, Any] | None:
    result = _rpc(
        "VideoLibrary.GetMovieDetails",
        {
            "movieid": library_id,
            "properties": ["title", "year", "imdbnumber", "uniqueid", "lastplayed", "genre"],
        },
    )
    details = result.get("moviedetails")
    if not isinstance(details, dict):
        return None
    entry: dict[str, Any] = {
        "media_type": "movie",
        "title": details.get("title", ""),
        "year": details.get("year"),
        "playcount": max(1, playcount),
    }
    entry.update(_extract_ids(details))
    watched_at = kodi_datetime_to_utc_iso(details.get("lastplayed", ""))
    if watched_at:
        entry["watched_at"] = watched_at
    genres = [genre.lower() for genre in (details.get("genre") or [])]
    if "anime" in genres:
        entry["anime"] = True
    return entry


def build_episode_import_entry(
    library_id: int,
    playcount: int,
) -> dict[str, Any] | None:
    result = _rpc(
        "VideoLibrary.GetEpisodeDetails",
        {
            "episodeid": library_id,
            "properties": ["showtitle", "season", "episode", "uniqueid", "lastplayed", "genre"],
        },
    )
    details = result.get("episodedetails")
    if not isinstance(details, dict):
        return None
    entry: dict[str, Any] = {
        "media_type": "episode",
        "title": details.get("showtitle", ""),
        "season": details.get("season"),
        "episode": details.get("episode"),
        "playcount": max(1, playcount),
    }
    entry.update(_extract_ids(details))
    watched_at = kodi_datetime_to_utc_iso(details.get("lastplayed", ""))
    if watched_at:
        entry["watched_at"] = watched_at
    genres = [genre.lower() for genre in (details.get("genre") or [])]
    if "anime" in genres:
        entry["anime"] = True
    return entry


def build_import_entry(event: dict[str, Any]) -> dict[str, Any] | None:
    try:
        if event["item_type"] == "movie":
            return build_movie_import_entry(event["library_id"], event["playcount"])
        return build_episode_import_entry(event["library_id"], event["playcount"])
    except (RuntimeError, KeyError, TypeError, ValueError) as exc:
        xbmc.log(f"[PunchPlay] Watched toggle detail fetch failed: {exc}", xbmc.LOGWARNING)
        return None
