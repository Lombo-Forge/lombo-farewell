# Troubleshooting

## Contents

- [Start with doctor](#start-with-doctor)
- [Messages](#messages)
- [The collection does not appear](#the-collection-does-not-appear)
- [A title is missing from the collection](#a-title-is-missing-from-the-collection)
- [Keep does nothing](#keep-does-nothing)
- [Turning it off](#turning-it-off)

## Start with doctor

`lombo-farewell doctor` checks, in order: Jellyfin answers, the key is accepted, the source can be read, and whether the collection exists. `[FAIL]` needs fixing.

## Messages

| Message | Meaning and fix |
|---|---|
| `[FAIL] settings: LOMBO_FAREWELL_...` | A setting is missing or wrong; the message names it. |
| `API key accepted: /System/Info: HTTP 401` | The key is wrong or was revoked. Create a new one. |
| `Jellyfin reachable: ...` fails | The URL is wrong, or the container cannot reach Jellyfin (different Docker network). |
| `source readable: Maintainerr ...` fails | Maintainerr is down, the URL is wrong, or its API changed. The collection was left as it was. |
| `source readable: cannot read ...` / `entry 3 ...` / `line 2 ...` | The list file is missing or has a line that cannot be understood. Fix the file; nothing was changed meanwhile. |
| `skipped: X: Jellyfin does not know this id` | The source names an id Jellyfin lacks (already deleted, or another library). |
| `skipped: X: no title with exactly this name in Jellyfin` / `2 titles with ...` | File source: use the Jellyfin `id` for this one. |
| `leave in place for now` | More titles went stale than `MAX_REMOVALS_PER_ROUND`; the rest are removed on later rounds. If many disappeared at once, check the source first. |
| `[WARN] ... shrank to N title(s)` | The list dropped to under half of the collection, so nothing was removed. Check the source (Maintainerr rules paused? library not mounted?); set `ALLOW_SHRINK=true` only if the drop is real. |
| `[WARN] the collection ... was not made by this tool` | A collection with that name already existed. Pick another `COLLECTION_NAME`, or set `ADOPT=true` to let the tool manage it (it removes titles that are not leaving). Also shown after the state file was deleted. |
| `the server redirected to ...` | The Jellyfin or Maintainerr URL answers with a redirect (often http to https). Set the URL to the final address; the API key is never sent to a redirect target. |
| `[warn] ... is not a setting` | A `LOMBO_FAREWELL_*` name is misspelled; the default is in use. |
| `[FAIL] webhook failed` | The alert was not sent; it is tried again next round. The collection was still updated. |
| `cannot write ... Mount a writable folder at /data` | `chown 999 data` on the host folder, or set `LOMBO_FAREWELL_STATE_FILE`. |

## The collection does not appear

It is made on the first **live** round (`DRY_RUN=false`) and only when at least one title is scheduled. `plan` says "would create it with N title(s)". If it still does not show, look for a Jellyfin library scan in progress, then refresh the client. It is unverified how every client shows it (see the README).

## A title is missing from the collection

Run `plan`: a skipped title is named with the reason. A title Jellyfin lists under another name than the file does will not match; use its `id`.

## Keep does nothing

`keep` refuses, with a reason, when: the source is `file` (nothing to tell), the name matches nothing or several titles, the title was already kept (its date is beyond the normal wait, or it was kept less than `KEEP_DAYS` days ago), or the source is down. In dry run it only says what it would do.

## Turning it off

Set `LOMBO_FAREWELL_DRY_RUN=true` and restart, or stop the container. The collection stays until you delete it in Jellyfin.
