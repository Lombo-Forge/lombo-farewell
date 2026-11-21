# How it is built

## Contents

- [The shape](#the-shape)
- [Module contracts](#module-contracts)
- [What came from the original and what changed](#what-came-from-the-original-and-what-changed)
- [Safe by default](#safe-by-default)
- [Settings](#settings)
- [Alerts](#alerts)
- [Adding a source](#adding-a-source)

## The shape

```
Maintainerr / list file --> sources.py --+
                                         v
Jellyfin <-- jellyfin.py <-- engine.py --> core.py (decides)
                                 ^
                              cli.py --> notify.py --> your webhook
```

`cli.py` reads the clock and prints; `engine.py` gathers and applies; `core.py` never touches the network, the disk or the clock.

## Module contracts

| Module | Takes | Returns / does |
|---|---|---|
| `core.plan(resolved, now, can_postpone)` | `(Candidate, jellyfin_id, title)` triples, a Unix time | `list[Entry]`, one per title (soonest deadline wins), soonest first, unknown dates last. `can_keep` is true only inside the normal wait. |
| `core.diff(have, want, max_removals, keep_ids)` | ids in the collection, ids wanted in order, ids the source lists but Jellyfin did not match | `Diff(add, remove, held_back)`. At most `max_removals` removals per round; `keep_ids` are never removed. The shrink guard and the owned-collection check are in `engine.sync`. |
| `core.find(entries, text)` | entries, what the person typed | `(entry, "")` or `(None, reason)`: exact name or id, then a unique part of a name; two matches is a refusal. |
| `core.keep_check(entry)` | an entry or None | `ok`, `not-leaving`, `already-kept`. |
| `core.overdue_to_alert(entries, already)` | entries, ids alerted before | titles 2+ days late and not yet reported, and the new remembered set. |
| `sources.MaintainerrSource` / `FileSource` | settings | `fetch() -> list[Candidate]`, `postpone(group, media_id, days) -> new deletion time`. Raise `SourceError` for anything unreadable. |
| `jellyfin.Jellyfin` | URL, key | `find_collection`, `collection_members`, `create_collection`, `add_to_collection`, `remove_from_collection`, `names`, `ids_named`. Raises `JellyfinError`. |
| `engine.build_entries(source, jf, now)` | | `(entries, skipped notes)` |
| `engine.sync(jf, cfg, entries, apply)` | | `SyncReport`; with `apply=False` nothing is written |
| `state` | a JSON file | `kept` titles and `alerted` ids; a damaged file means "start fresh". Also writes `leaving.json` (the contract in [contracts.md](contracts.md)). |
| `core.leaving_rows(entries, kept, now, keep_days)` | entries, the `kept` notes | the rows of `leaving.json` and `GET /v1/leaving`; `can_keep` includes the keep limit |
| `cli.keep_title(cfg, query, now)` | | `(done, message)`: the one keep rule, used by `keep` and the API (caller holds the run lock) |
| `api.make_server(cfg, keep, leaving)` | two functions from `cli.py` | an HTTP server (not started). Only HTTP, auth and limits live here; see [security.md](security.md) |

A **source** is the adapter point. Anything that can produce `Candidate` values (a Jellyfin id or a title, a deletion time if known, and the wait in days if it can postpone) can be a source.

## What came from the original and what changed

| Original | Here |
|---|---|
| Plan from Maintainerr collections: `deleteAfterDays` after `addDate`, soonest first, one entry per title, `can_keep` only inside the normal wait | kept as is (`core.plan`, `sources.maintainerr_candidates`) |
| Collection "holds exactly" the list; adds first, then removes, in batches of 50 | kept; plus a removal cap per round, no reordering, and no empty collection is ever created |
| Overdue title (2+ days) alerted once, forgotten when it leaves | kept (`core.overdue_to_alert`) |
| "Keep it longer" button in a page script, answered by a server, with a lock | `lombo-farewell keep` (one process at a time, by the run lock) and the optional `lombo-farewell serve` API for a page to call; the script itself is not part of this app |
| Per-title labels ("Gone Friday") drawn by the page script | dropped: no script; dates are in `plan` |
| Database for keeps and alert memory | a small JSON state file |
| Maintainerr only | Maintainerr or a list file |

## Safe by default

- `DRY_RUN` is true until the person sets it to false. `plan` never writes anything.
- The app has no call that deletes a title or file. The only deletes are `DELETE /Collections/{id}/Items`, which unlist titles from the collection.
- A source that cannot be read, or a Jellyfin that errors, ends the round before any write. A failure part-way through a write cannot shorten the collection, because additions go first.
- A bad list file (one bad line) is an error for the whole file, never a half list.
- `doctor` separates "Jellyfin is unreachable" from "the key was refused" and checks the source.
- A bad setting stops the app at start-up and names the variable.
- In `--loop` mode one failed round is printed and the next one still runs.

## Settings

Every setting is an environment variable with the `LOMBO_FAREWELL_` prefix, validated in `config.py` and listed in `SETTINGS`. To add one: read it in `config.py` with a clear error, add it to `SETTINGS` and `.env.example` (a test keeps the three in step), and add a test.

## Alerts

`notify.send(url, message)` posts JSON with the text under `content` and `text`, which Discord, Slack and many bridges accept. Nothing is hard-coded to a service. An alert is sent once for a title that is 2 or more days past its deletion date and still listed (the cleanup tool may be stuck), only when `DRY_RUN=false` and a webhook is set.

## Adding a source

Write a class with `can_postpone`, `fetch()` and `postpone()` in `sources.py`, raise `SourceError` for anything odd, return it from `engine.make_source`, add a `SOURCE` value and the settings it needs to `config.py`, and test it with a fake. A source must never return a partial list.
