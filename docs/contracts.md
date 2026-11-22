# Contracts other apps may rely on

## Contents

- [The leaving list (`leaving.json` and `GET /v1/leaving`)](#the-leaving-list)
- [The keep call (`POST /v1/keep`)](#the-keep-call)
- [The keep history (`keeps.json` and `GET /v1/keeps`)](#the-keep-history)
- [The post-keep command](#the-post-keep-command)
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
    {"id": "0123456789abcdef0123456789abcdef", "title": "Alpha Movie", "days_left": 1, "can_keep": true,
     "delete_at": 1800086400, "overdue_days": 0, "group": "7", "group_name": "Old movies", "kept": null},
    {"id": "fedcba9876543210fedcba9876543210", "title": "Beta Show", "days_left": 35, "can_keep": false,
     "delete_at": 1803024000, "overdue_days": 0, "group": "8", "group_name": null,
     "kept": {"by": "Ann", "at": 1800000000, "until": 1803024000}}
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
| `can_keep` | `true` when a keep would be accepted right now: the title is inside its normal wait, was not kept lately, and the source can postpone. The optional keep limits (`MAX_KEEPS_PER_PERSON`, `MAX_KEEPS_PER_TITLE`) are **not** reflected here: the first depends on who asks, the second is only checked when a keep is tried. A keep over a limit is a `422`. |
| `delete_at` | Unix time in seconds when the source will delete the title, or `null` when it gives no date. After a keep it is the new date. |
| `overdue_days` | Whole days the title is past `delete_at` and still listed (the cleanup tool should have deleted it); `0` otherwise. |
| `group` | The source's own id for the group the title sits in (a Maintainerr collection id, as text), or `null` (the file source). Use it to tell which cleanup rule lists a title. |
| `group_name` | That group's name as the source gives it (the Maintainerr collection `title`), cleaned like `title`; `null` when the source gives none. |
| `kept` | `null` for a title that was never kept while it stayed on the list. Otherwise `{"by": <name or null>, "at": <Unix time of the keep>, "until": <the date it was moved to, or null>}`. It stays after the extra time has run out (read `can_keep` for what is allowed now) and is dropped when the title leaves the list. `by` is whatever the caller sent in the keep (cleaned like `title`); `null` when nobody said. |

Fields after `can_keep` were added in v0.3 without a version bump: they are optional additions (see
[what a version bump means](#what-a-version-bump-means)). A reader of the four older fields keeps working.
Types: `delete_at`, `kept.at` and `kept.until` are whole numbers or `null`; `overdue_days` is a whole number;
`group` and `group_name` are strings or `null`.

**The file** is `leaving.json`, in the same folder as `LOMBO_FAREWELL_STATE_FILE` (`/data/leaving.json` by default). It is
rewritten after every round that changes things, that is with `LOMBO_FAREWELL_DRY_RUN=false`: a dry run writes no
files. It is written to a temporary name and renamed, so a reader sees the old file or the new one, never half of one.
It is readable by every user on the machine (mode 0644) because other apps read it; it holds titles, dates and the
names given as `by`, never a token or a key. If it cannot be written, the
round still succeeds and prints a warning. A reader should treat a file older than a few intervals
(`LOMBO_FAREWELL_INTERVAL_SECONDS`) as stale, and a missing file as "nothing known".

**The API** returns the same document from `GET /v1/leaving`, made fresh at the moment of the request (so it also works in
dry run). `can_keep` there also reflects a keep done since the last round.

## The keep call

`POST /v1/keep` with `Content-Type: application/json` and a body of `{"title": "Alpha Movie"}`: a title (or a
unique part of one) or a Jellyfin id, 1 to 200 printable characters. One optional second key, `"by"`: who is keeping
it, a string of 1 to 60 printable characters with no space at either end (for example the viewer's Jellyfin name,
put there by your proxy or your admin page). No other keys. `by` is recorded in the state file, in the keep
history and in `leaving.json`, and it is what `MAX_KEEPS_PER_PERSON` counts. The token proves the *caller*, not the
person, so `by` is only as true as the program that sends it ([security.md](security.md)). It runs the same check and postpone
as `lombo-farewell keep`, and returns `{"ok": true|false, "message": "..."}`.

| Status | Meaning |
|---|---|
| 200 | `ok` is `true`. In dry run the message says what would be done and nothing changed. |
| 422 | Refused by a rule: no such title, several match, already kept, cannot postpone with this source, or over a keep limit (a person who has used their keeps, a title kept too often, or no `by` while `MAX_KEEPS_PER_PERSON` is set). `ok` is `false`; the message says why. |
| 502 | Jellyfin, Maintainerr or the state file could not be used. Nothing was changed. The message is generic; the log has the detail. |
| 400, 411, 413, 415 | The request itself was wrong: not strict JSON or wrong shape, no `Content-Length`, body over 4096 bytes, not `application/json`. |

## The keep history

Every keep that really postponed a title (never a refusal, never a dry run) adds one row to `keeps.json`, in the same
folder as the state file (mode 0644, written atomically, newest last, at most the newest 500 rows). `GET /v1/keeps`
returns the same document, read at the moment of the request. A missing or damaged file reads as no history.

```json
{"version": 1, "keeps": [
  {"at": 1800000000, "id": "0123456789abcdef0123456789abcdef", "title": "Alpha Movie", "by": "Ann", "until": 1802592000}
]}
```

`at` is the Unix time of the keep, `until` the new deletion date, `by` the name sent with the keep or `null`. The
history is the record of who kept what and when; `leaving.json` only holds the current keep of each listed title. The
same rules for versions apply: new optional fields may appear, readers ignore what they do not know.

## The post-keep command

- **Name:** `LOMBO_FAREWELL_POST_KEEP_COMMAND`. Empty (the default) means off.
- **When it runs:** once after a keep that really postponed a title, from `lombo-farewell keep` and from
  `POST /v1/keep`. Never in a dry run, never when the keep is refused or fails. It runs after the run lock is
  released.
- **How:** split with `shlex`, started without a shell, no input, output dropped, 60 second limit.
- **What it gets:** the normal environment plus `LOMBO_FAREWELL_KEPT_JSON`:

```json
{"id": "0123456789abcdef0123456789abcdef", "title": "Alpha Movie", "by": "Ann", "until": 1802592000}
```

  `id` is the Jellyfin item id (32 lower-case hex digits, no dashes); `title` is the cleaned title; `by` is the name
  sent with the keep or `null`; `until` is the new deletion date as a Unix time. (`by` and `until` were added in
  v0.3: ignore keys you do not know.)
- **Failure never fails the action:** a non-zero exit, a timeout or a command that cannot start gives a note and
  nothing else changes. The CLI prints `[warn] post-keep command failed (exit 1)` to standard error and still exits
  0. The API still answers `200` with `"ok": true`, and `message` ends with `Note: post-keep command failed (exit 1).`

## Other routes and status codes

- `GET /v1/health` returns `{"ok": true}`.
- `GET /v1/keeps` returns the keep history above.
- Every request needs `Authorization: Bearer <token>`. Missing or wrong is `401` with the body `{"ok": false, "message": "unauthorized"}` and nothing else. After 10 bad tries in a minute from one address, `429` with `Retry-After` (even for the right token, until the minute is up).
- An unknown path is `404`, the wrong method on a known path is `405` with an `Allow` header. Both are only answered to a caller with a good token.
- There are no CORS headers and no cookies. How a browser page should reach this is in [security.md](security.md).

## What a version bump means

- **Same version:** a new, optional field may be added to an entry or to the top level (also to a `kept` object, a
  keep-history row, or the post-keep command's JSON), or a new route or optional request key (the `by` of a keep). A reader must ignore fields it does not know.
- **`version` goes up:** a field was removed or renamed, its meaning or type changed, or the order of `entries` changed. A reader that does not know the new number should treat the file as unreadable instead of guessing. The old shape is announced in the release notes and kept for at least one release where that is possible (for example by serving `/v1` and `/v2` together); the file has only one shape at a time.
- The route prefix `/v1` and the file `version` move together.
