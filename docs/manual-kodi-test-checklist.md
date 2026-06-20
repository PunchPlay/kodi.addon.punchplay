# Manual Kodi Test Checklist

Use this checklist before calling a `1.3.x` build release-ready.

## Matrix

- Kodi Nexus 20
- Kodi Omega 21
- Windows
- Linux
- Android TV / Fire TV
- macOS

## Account

- Fresh install opens cleanly and starts the background service.
- QR login completes and closes automatically after approval.
- `Test PunchPlay Connection` reports connected account details.
- Logout with an empty queue succeeds immediately.
- Logout with queued events shows the destructive warning first.
- Expired access token refreshes without requiring a new login.
- Invalid backend URL is rejected and does not send tokens.

## Playback

- Movie start, progress, pause, resume, and stop below threshold.
- Movie stop above threshold marks watched exactly once.
- Episode stop above threshold marks watched exactly once.
- Seek forward during playback updates progress correctly.
- Seek backward during playback does not break final stop behavior.
- Stop immediately after playback start creates an incomplete session only.
- Natural playback end emits one authoritative final stop.
- Duplicate `onPlayBackStopped` and `onPlayBackEnded` callbacks do not duplicate history.
- Autoplay next episode does not show a rating prompt over the next item.
- Kodi shutdown during playback does not leave the heartbeat running.

## Offline

- Start online, disconnect, stop playback, reconnect, and confirm replay.
- Start offline while already logged in, finish playback, reconnect, and confirm replay.
- Replayed queued events preserve `event_id` and do not duplicate history.
- Completed items do not return to continue-watching after stale progress replay.
- Queue clear action works and status counts update immediately.

## Matching

- Kodi library movie with TMDB or IMDb ID skips backend identify.
- Kodi library episode with TMDB or TVDB ID skips backend identify.
- Loose movie filename with year matches correctly.
- Loose TV `SxxExx` filename matches correctly.
- Season-folder numeric episode filename matches correctly.
- Anime absolute-episode filename matches correctly.
- Multi-episode filename sends `episode_end` and `multi_episode`.
- Poor filename falls back without breaking scrobbling.

## Library Sync

- `Preview Library Import` returns counts without writing history.
- Preview diagnostics file is written when unmatched or failed items exist.
- Real library import reports imported, skipped duplicates, unmatched, and failed counts.
- Cancelled import stops cleanly without freezing Kodi.

## Ratings

- Completed movie can be rated after the delay.
- `Later` skips one prompt without suppressing future prompts.
- `Never for this title` suppresses the same item later.
- `Never for this show` suppresses later episodes of the same show.
- `Disable rating prompts` updates the addon setting.

## Debug

- `Show Status` displays backend validity, queue summary, and identify cache size.
- Basic debug export does not include tokens or file paths.
- Verbose debug export warns before including queued file paths.
- Exported debug file is written into addon data and the shown path is correct.
