# Manual Kodi Test Checklist

Use this checklist before calling a `1.5.x` build release-ready.

Sections marked **[1.5.1]** cover behaviour introduced in that release that
cannot be verified by unit tests. Keep them in the regression pass.

## Matrix

- Kodi Nexus 20
- Kodi Omega 21
- Windows
- Linux
- Android TV / Fire TV
- macOS

Nexus 20 on Windows matters most: it ships Python 3.8, the oldest runtime we
support.

### Simulating a slow or dead backend

Several checks below need the backend to hang rather than refuse. Point
`backend_url` at a black-holed address so requests time out instead of failing
fast:

- Hangs (use for stall tests): `https://10.255.255.1`
- Refuses immediately (use for offline-queue tests): `https://127.0.0.1:9`

Both need `developer_mode` enabled. Restore the real URL afterwards.

## Account

- Fresh install opens cleanly and starts the background service.
- QR login completes and closes automatically after approval.
- `Test PunchPlay Connection` reports connected account details.
- Logout with an empty queue succeeds immediately.
- Logout with queued events shows the destructive warning first.
- Expired access token refreshes without requiring a new login.
- Invalid backend URL is rejected and does not send tokens.

## Login polling **[1.5.1]**

- Approving on the phone still closes the QR window within ~5s.
- Dismiss the QR window without approving, then approve on the phone: the
  fallback progress dialog completes the login.
- **Dismiss the QR window and watch the countdown.** It must continue from the
  original expiry, not restart at the full 10 minutes. A restarted countdown
  means the shared deadline regressed.
- Let a code expire untouched: the failure message names the expiry, and no
  polling continues afterwards (check the log).
- Start several logins back to back from the same network until the backend
  throttles. The dialog must report being rate limited, **not** a generic
  timeout, and the log should show a back-off wait rather than continuous
  polling.

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

## Playback with a stalled backend **[1.5.1]**

This is the core regression risk in 1.5.1: scrobble posts moved off Kodi's
player callback thread. Point `backend_url` at the hanging address first.

- Start playback. Video must begin immediately — no multi-second freeze before
  the picture appears.
- Pause and resume repeatedly. The UI must stay responsive throughout; any
  stutter or unresponsive remote means work is back on the callback thread.
- Stop playback and immediately start the next item. The next video must start
  without waiting on the previous stop.
- Let an episode finish and autoplay the next. The transition must not stall.
- With a large offline queue (30+ events), start playback: it must begin
  promptly. The queue drains from the service loop, not at playback start.
- Restore the real backend and confirm the queued events replay intact.

## Playback event delivery **[1.5.1/1.5.2]**

- Watch a movie start to finish and confirm the backend received start,
  progress, and stop **in that order**.
- Stop a movie above threshold, then quit Kodi within a second or two. The stop
  event must still arrive — shutdown waits for the queued post.
- With the backend black-holed, stop a movie and quit Kodi after the post has
  started. Restore the backend and restart Kodi: the in-flight stop and any
  queued events must replay in timestamp order.
- Force-kill Kodi mid-playback. No duplicate or corrupt history; a lost
  in-flight event is acceptable here, duplicated history is not.
- Confirm the rating prompt still appears after a watched stop. It is queued
  from the worker now and shown by the service loop.

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

## Library sync failure reporting **[1.5.1]**

Needs a library larger than one batch (100+ watched items) to be meaningful.

- Run a full sync against a healthy backend on a large library. It must run to
  completion — no stall partway — and the reported total must match what
  actually landed on PunchPlay.
- Kill the backend partway through a sync. The result must be the partial
  message naming how many items could not be sent, with a warning icon —
  **not** a success notification with a reduced total.
- Leave the backend down and start a sync. It must give up after three failed
  batches rather than grinding through the whole library.
- Re-run the sync once the backend is healthy: previously unsent items import,
  and already-imported ones come back as skipped duplicates rather than
  duplicating history.
- Check `library-import-diagnostics.json` records `failed_batches` and
  `not_sent`.
- Preview mode still aborts on the first failure with an error, rather than
  reporting partial counts.

## Two-way sync

Not previously covered by this checklist; added for 1.4.0/1.5.0 features.

- `Sync From PunchPlay Now` applies watched state and resume points, and never
  un-watches a locally watched item.
- A resume point is only overwritten when the PunchPlay progress is newer.
- Cancelling a pull sync does not record the run, so the next auto-sync
  re-covers the unfinished remainder.
- A Kodi library write failure is reported and retried; a newly failing item
  receives three attempts of its own before the checkpoint can advance.
- Marking an item watched in the Kodi library pushes to PunchPlay within
  seconds.
- Un-watching in Kodi does **not** delete PunchPlay history.
- Marking a whole season watched batches into a single import.
- Playing an item to completion does not double-count: the stop scrobble and
  Kodi's own playcount bump must produce one watch, not two.
- A pull sync's own writes do not echo back as manual watched toggles.
- A library scan triggers a pull sync, at most once per 30 minutes.

## Ratings

- Completed movie can be rated after the delay.
- `Later` skips one prompt without suppressing future prompts.
- `Never for this title` suppresses the same item later.
- `Never for this show` suppresses later episodes of the same show.
- `Disable rating prompts` updates the addon setting.
- `Clear Rating Prompt Suppressions` restores prompts for suppressed items.

## Debug

- `Show Status` displays backend validity, queue summary, and identify cache size.
- Basic debug export does not include tokens or file paths.
- Verbose debug export warns before including queued file paths.
- Exported debug file is written into addon data and the shown path is correct.
