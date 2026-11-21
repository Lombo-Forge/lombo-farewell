# Contracts other apps may rely on

## Contents

- [The leaving list (`leaving.json` and `GET /v1/leaving`)](#the-leaving-list)
- [The keep call (`POST /v1/keep`)](#the-keep-call)
- [Other routes and status codes](#other-routes-and-status-codes)
- [What a version bump means](#what-a-version-bump-means)

Everything below is a promise to other apps (a page script, a dashboard, another Lombo-Forge app). If you change
it, change this file in the same pull request. Nothing here was tried against a real client.

## The leaving list

The same document is written to a file and served over HTTP.

```json
{
  "version": 1,
  "updated": 1800000000,
  "entries": [
    {"id": "0123456789abcdef0123456789abcdef", "title": "Alpha Movie", "days_left": 1, "can_keep": true},
    {"id": "fedcba9876543210fedcba9876543210", "title": "Beta Show", "days_left": 35, "can_keep": false}
  ]
}
```

| Field | Meaning |
|---|---|
| `version` | Whole number, `1` today. See [what a version bump means](#what-a-version-bump-means). |
| `updated` | Unix time in seconds when the list was made. |
| `entries` | Titles about to be deleted that Jellyfin knows, **soonest first**; titles with no known date are last. Empty when nothing is leaving. |
| `id` | The Jellyfin item id: 32 lower-case hex characters, no dashes. |
| `title` | The title as Jellyfin names it, with control characters and `< > @ [ ]` replaced by `?`. |
| `days_left` | Whole days until deletion, rounded up, never below 0 (an overdue title is `0`). `null` when the source gives no date. |
| `can_keep` | `true` when a keep would be accepted right now: the title is inside its normal wait, was not kept lately, and the source can postpone. |

**The file** is `leaving.json`, in the same folder as `LOMBO_FAREWELL_STATE_FILE` (`/data/leaving.json` by default). It is
rewritten after every round that changes things, that is with `LOMBO_FAREWELL_DRY_RUN=false`: a dry run writes no
files. It is written to a temporary name and renamed, so a reader sees the old file or the new one, never half of one.
It is readable by every user on the machine (mode 0644) because it holds titles only. If it cannot be written, the
round still succeeds and prints a warning. A reader should treat a file older than a few intervals
(`LOMBO_FAREWELL_INTERVAL_SECONDS`) as stale, and a missing file as "nothing known".

**The API** returns the same document from `GET /v1/leaving`, made fresh at the moment of the request (so it also works in
dry run). `can_keep` there also reflects a keep done since the last round.

## The keep call

`POST /v1/keep` with `Content-Type: application/json` and a body of exactly `{"title": "Alpha Movie"}`: a title (or a
unique part of one) or a Jellyfin id, 1 to 200 printable characters. No other keys. It runs the same check and postpone
as `lombo-farewell keep`, and returns `{"ok": true|false, "message": "..."}`.

| Status | Meaning |
|---|---|
| 200 | `ok` is `true`. In dry run the message says what would be done and nothing changed. |
| 422 | Refused by a rule: no such title, several match, already kept, cannot postpone with this source. `ok` is `false`; the message says why. |
| 502 | Jellyfin, Maintainerr or the state file could not be used. Nothing was changed. The message is generic; the log has the detail. |
| 400, 411, 413, 415 | The request itself was wrong: not strict JSON or wrong shape, no `Content-Length`, body over 4096 bytes, not `application/json`. |

## Other routes and status codes

- `GET /v1/health` returns `{"ok": true}`.
- Every request needs `Authorization: Bearer <token>`. Missing or wrong is `401` with the body `{"ok": false, "message": "unauthorized"}` and nothing else. After 10 bad tries in a minute from one address, `429` with `Retry-After` (even for the right token, until the minute is up).
- An unknown path is `404`, the wrong method on a known path is `405` with an `Allow` header. Both are only answered to a caller with a good token.
- There are no CORS headers and no cookies. How a browser page should reach this is in [security.md](security.md).

## What a version bump means

- **Same version:** a new, optional field may be added to an entry or to the top level. A reader must ignore fields it does not know.
- **`version` goes up:** a field was removed or renamed, its meaning or type changed, or the order of `entries` changed. A reader that does not know the new number should treat the file as unreadable instead of guessing. The old shape is announced in the release notes and kept for at least one release where that is possible (for example by serving `/v1` and `/v2` together); the file has only one shape at a time.
- The route prefix `/v1` and the file `version` move together.
