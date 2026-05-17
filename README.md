# PunchPlay Scrobble for Kodi

PunchPlay Scrobble is a Kodi service addon that tracks movies and TV episodes you watch in Kodi and sends them to your [PunchPlay.tv](https://punchplay.tv) account.

It supports Kodi Nexus 20 and Omega 21.

## Install

1. Download the latest addon zip:
   [script.punchplay.zip](https://github.com/PunchPlay/script.punchplay/releases/latest/download/script.punchplay.zip)
2. In Kodi, open **Settings -> Add-ons -> Install from zip file**.
3. Choose the downloaded zip.
4. Kodi installs the addon and starts the background service automatically.

If Kodi blocks zip installs, enable **Settings -> System -> Add-ons -> Unknown sources** first.

## Connect Your Account

1. In Kodi, open **Settings -> Add-ons -> My add-ons -> Services -> PunchPlay Scrobble -> Configure**.
2. Click **Login to PunchPlay**.
3. Scan the QR code or visit `https://punchplay.tv/link`.
4. Sign in and approve the code shown in Kodi.
5. The Kodi dialog closes automatically once the device is connected.

Tokens are stored locally in Kodi's addon data directory and refreshed automatically. Use **Logout** in the addon settings to disconnect the device and clear pending offline scrobbles.

## What It Tracks

The addon sends these playback events to PunchPlay:

| Kodi event | PunchPlay endpoint | Purpose |
| --- | --- | --- |
| Playback starts | `/api/scrobble/start` | Creates or updates now-playing progress |
| Pause | `/api/scrobble/pause` | Saves the current position |
| Resume | `/api/scrobble/resume` | Marks the item active again |
| Progress heartbeat | `/api/scrobble/progress` | Updates continue-watching progress |
| Stop or end | `/api/scrobble/stop` | Saves progress or logs a completed watch |

The default watched threshold is 70%. When playback reaches the configured threshold, PunchPlay adds the item to your watch history. Stops below the threshold are kept as continue-watching progress.

## Current Features

- Automatic movie and TV episode scrobbling.
- Continue-watching progress on PunchPlay.
- Post-watch rating dialog for movies and episodes.
- One-click import of watched Kodi library items.
- QR/device-code login.
- Token refresh on expired access tokens.
- Offline queue for network failures.
- Per-playback session IDs to prevent stale queued progress from reappearing after a completed stop.
- Anime toggle based on Kodi's `anime` genre tag.

## Settings

Open **Configure** from the addon details page.

| Setting | Default | Notes |
| --- | --- | --- |
| Backend URL | `https://punchplay.tv` | Hidden by default. Leave unchanged unless testing another backend. |
| Login to PunchPlay | - | Starts QR/device-code login. |
| Logout | - | Clears tokens and queued offline scrobbles. |
| Scrobble movies | On | Enables movie tracking. |
| Scrobble TV shows | On | Enables episode tracking. |
| Scrobble anime | On | Applies to episodes with the `anime` genre. |
| Rate after watching | On | Shows the PunchPlay rating dialog after a completed scrobble. |
| Show scrobble notifications | On | Shows Kodi notifications for completed scrobbles. |
| Show notifications during playback | Off | Keeps notifications quiet while another video is already playing. |
| Watched threshold (%) | 70 | Minimum progress needed to log a completed watch. |
| Minimum file length (minutes) | 5 | Ignores trailers and short clips. |
| Heartbeat interval (seconds) | 30 | Frequency of progress posts. Position is cached more often internally for reliable stop handling. |
| Sync Kodi Library | - | Imports watched movies and episodes from Kodi's local library. |

## Media Matching

PunchPlay Scrobble identifies media in this order:

1. Kodi library metadata from `InfoTagVideo`, including TMDB, TVDB, IMDb, season, and episode IDs when Kodi has them.
2. Filename parsing for common movie and episode names such as `Show.S01E02.1080p.WEB-DL.mkv`.
3. Server-side PunchPlay/TMDB matching when the addon only has a title or filename.

Keeping your Kodi library scraped with TMDB-compatible IDs gives the most accurate results.

## Offline Behavior

If PunchPlay cannot be reached, scrobble events are written to a local SQLite queue in Kodi addon data. The queue is replayed in order when the connection returns.

The queue is capped at 200 events. Completed playback sessions clear their older queued progress events before sending the final stop event, which prevents old progress from bringing a watched item back into continue-watching.

## Library Sync

Use **Configure -> Library -> Sync Kodi Library** to import existing watched items from Kodi into PunchPlay.

The sync reads watched movies and episodes from Kodi's video library using JSON-RPC, sends them in batches, and skips duplicates server-side.

## Development Checks

Before publishing a release:

```bash
python3 -m py_compile api.py cache.py default.py identifier.py login_dialog.py player.py rating_dialog.py service.py
kodi-addon-checker --branch omega script.punchplay/
```

Build the zip from the parent directory:

```bash
zip -r /tmp/script.punchplay.zip script.punchplay \
  -x 'script.punchplay/.git/*' \
     'script.punchplay/__pycache__/*' \
     'script.punchplay/*/__pycache__/*' \
     'script.punchplay/.DS_Store' \
     'script.punchplay/**/.DS_Store'
```

Upload the asset as `script.punchplay.zip` on the latest GitHub release. The PunchPlay website download button points at:

```text
https://github.com/PunchPlay/script.punchplay/releases/latest/download/script.punchplay.zip
```

## File Layout

```text
script.punchplay/
├── addon.xml
├── default.py
├── service.py
├── player.py
├── api.py
├── identifier.py
├── cache.py
├── login_dialog.py
├── rating_dialog.py
├── icon.png
├── fanart.jpg
├── changelog.txt
├── LICENSE.txt
└── resources/
    ├── settings.xml
    ├── media/background.png
    └── language/resource.language.en_gb/strings.po
```

## License

GPL-2.0-only. See [LICENSE.txt](LICENSE.txt).
