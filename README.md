# PunchPlay Scrobble — Kodi Addon

Automatically tracks movies and TV episodes you watch in Kodi and posts them to your **PunchPlay.tv** account in real time.

Supported Kodi versions: **Nexus (20)** and **Omega (21)**, Python 3 only.

---

## Installation

### 1. Bundle guessit (one-time setup)

The addon uses [guessit](https://github.com/guessit-io/guessit) to parse filenames for items that aren't in your Kodi library.  Because Kodi addons can't `pip install` at runtime, you bundle guessit and its runtime dependencies inside the addon.

```bash
# From inside the script.punchplay/ directory:
pip install guessit --target lib/ --no-deps
pip install babelstone rebulk --target lib/ --no-deps   # guessit runtime deps
```

If you skip this step the addon still works — it falls back to a built-in regex parser.  Identification quality will be lower for files not in the Kodi library.

### 2. Zip the addon

```bash
# From the parent directory (one level above script.punchplay/):
zip -r script.punchplay.zip script.punchplay/ \
  --exclude "*.pyc" --exclude "*/__pycache__/*"
```

### 3. Sideload into Kodi

1. Copy `script.punchplay.zip` to the device running Kodi (USB, network share, or `adb push`).
2. In Kodi: **Settings → Add-ons → Install from zip file**.
3. Navigate to the zip and confirm.  Kodi will install and start the service immediately.

---

## Configuration

Open **Settings → Add-ons → My add-ons → Services → PunchPlay Scrobble → Configure**.

| Setting | Default | Description |
|---|---|---|
| **Backend URL** | `https://punchplay.tv` | Base URL of your PunchPlay API. Change only if self-hosting. |
| **Watched threshold (%)** | 70 | Minimum play percentage before an item is marked as "watched". |
| **Minimum file length (min)** | 5 | Files shorter than this are ignored (trailers, clips). |
| **Heartbeat interval (sec)** | 30 | How often progress is reported during playback. |
| **Scrobble movies** | On | Toggle movie tracking. |
| **Scrobble TV shows** | On | Toggle TV episode tracking. |
| **Scrobble anime** | On | Toggle anime tracking (detected by genre tag). |

---

## Logging In

1. Open the addon settings.
2. Click **Login to PunchPlay**.
3. A dialog will show a short code and a URL, e.g.:

   ```
   Visit: https://punchplay.tv/link
   Enter code: ABCD-1234
   ```

4. Open the URL on any device, enter the code, and approve the request.
5. Kodi polls automatically — you'll see a "Login successful!" notification within seconds.

Tokens are stored in the Kodi addon data directory (`userdata/addon_data/script.punchplay/`).  They are refreshed automatically; you only need to log in once.

To log out, click **Logout** in the addon settings.

---

## How It Works

```
Kodi player event
       │
       ▼
  PunchPlayPlayer (player.py)
       │  identify via Kodi library → identifier.py
       │  fallback: guessit / regex parse → identifier.py
       │  cache lookup/store → cache.py (SQLite)
       │
       ▼
  APIClient (api.py)
       │  POST /scrobble/start|pause|resume|stop|progress
       │  Bearer token attached automatically
       │  401 → refresh token, retry once
       │  network error → write to pending_scrobbles (SQLite)
       │
       ▼
  PunchPlay REST API
```

### Scrobble events

| Event | Endpoint | Triggered when |
|---|---|---|
| Start | `POST /scrobble/start` | Playback begins (`onAVStarted`) |
| Pause | `POST /scrobble/pause` | Player paused |
| Resume | `POST /scrobble/resume` | Player resumed |
| Stop | `POST /scrobble/stop` | User stops or file ends |
| Progress | `POST /scrobble/progress` | Every N seconds during playback |

All requests share the same JSON schema:

```json
{
  "media_type": "episode",
  "title": "Breaking Bad",
  "year": 2008,
  "imdb_id": "tt0903747",
  "tmdb_id": 1396,
  "tvdb_id": 81189,
  "season": 1,
  "episode": 1,
  "progress": 0.72,
  "duration_seconds": 2640,
  "position_seconds": 1901,
  "device_id": "uuid-stored-per-device",
  "client_version": "1.0.0"
}
```

Optional fields (`imdb_id`, `tmdb_id`, `tvdb_id`, `season`, `episode`, `year`) are omitted when unavailable.  A `raw_filename` field is included instead of `title` when identification completely fails, so the server can do its own lookup.

Stop events add `"watched": true` when the play percentage reaches or exceeds the configured threshold.

### Offline resilience

Every failed POST is written to a local SQLite table (`pending_scrobbles`).  The service flushes the queue every 60 seconds and also immediately before each new `start` event.  Events are replayed in insertion order; unrecoverable server errors (4xx) are discarded so they don't block subsequent events.

---

## File layout

```
script.punchplay/
├── addon.xml               Addon metadata & extension points
├── default.py              Entry point (service start or settings action)
├── service.py              xbmc.Monitor — main service loop
├── player.py               xbmc.Player — playback event handlers + heartbeat
├── api.py                  HTTP client (auth, retry, offline queue)
├── identifier.py           Media identification pipeline
├── cache.py                SQLite: identifier cache + offline queue
├── lib/                    Bundled Python packages (guessit, etc.)
│   └── guessit/
│       └── ...
└── resources/
    └── settings.xml        Addon settings UI
```

---

## Backend API requirements

Your PunchPlay API must implement:

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/device/code` | Returns `{ user_code, verification_uri, device_code, expires_in }` |
| `POST` | `/auth/device/token` | Body: `{ device_code }`. Returns `{ access_token, refresh_token }` or `{ error }` |
| `POST` | `/auth/refresh` | Body: `{ refresh_token }`. Returns new token pair. |
| `POST` | `/scrobble/start` | Scrobble payload |
| `POST` | `/scrobble/pause` | Scrobble payload |
| `POST` | `/scrobble/resume` | Scrobble payload |
| `POST` | `/scrobble/stop` | Scrobble payload (may include `"watched": true`) |
| `POST` | `/scrobble/progress` | Scrobble payload |

The API should return `401` when a token is expired so the addon can refresh automatically.

---

## License

MIT
