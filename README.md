<p align="center"><img src="assets/banner.svg" alt="LomboFarewell" width="100%"></p>

<p align="center">
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/github/license/Lombo-Forge/lombo-farewell"></a>
  <a href="https://github.com/Lombo-Forge/lombo-farewell/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/Lombo-Forge/lombo-farewell/ci.yml?branch=main&label=CI"></a>
  <a href="https://github.com/Lombo-Forge/lombo-farewell/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://img.shields.io/github/actions/workflow/status/Lombo-Forge/lombo-farewell/codeql.yml?branch=main&label=CodeQL"></a>
  <a href="https://github.com/Lombo-Forge/lombo-farewell/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Lombo-Forge/lombo-farewell?include_prereleases"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <a href="https://ko-fi.com/datlombardo"><img alt="Support on Ko-fi" src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=ko-fi&logoColor=white"></a>
</p>

# LomboFarewell

Puts the titles that a cleanup tool is about to delete into a **"Leaving soon" collection** in Jellyfin, so viewers can see them in any app, and lets you postpone a deletion with one command. It only mirrors the list and postpones; it never deletes anything itself.

> **Status: not run against real services.** LomboFarewell has been tested only against fake Jellyfin and Maintainerr servers. See [What is not verified](#what-is-not-verified). Keep it in dry-run mode until `plan` shows what you expect.

```text
$ lombo-farewell plan
leaving: Alpha Movie (1 day(s) left)
leaving: Beta Show (12 day(s) left)
collection: would create it with 2 title(s)
```

## Contents

- [What it does and does not do](#what-it-does-and-does-not-do)
- [Install in a minute](#install-in-a-minute)
- [Commands](#commands)
- [Where the list comes from](#where-the-list-comes-from)
- [Keeping a title longer](#keeping-a-title-longer)
- [Settings](#settings)
- [What is not verified](#what-is-not-verified)
- [FAQ](#faq)
- [Contributing and licence](#contributing-and-licence)

## What it does and does not do

Every round (hourly by default) it asks a **source** which titles are scheduled for deletion, finds them in Jellyfin, and makes a collection called "Leaving soon" hold exactly those titles, soonest first. The collection is a normal Jellyfin collection, so every client shows it, with no plugin and no script injected into the web page.

It **never**: deletes a title or a file (it has no such call; it can only add titles to the collection and take them out of it); changes the cleanup tool's rules; or (with its default settings) empties the collection by accident. If the source cannot be read (tool down, file missing or half-written, odd reply) the round stops and the collection stays as it is. If the list shrinks to nothing, or to under half of what the collection holds, **nothing is removed** and a warning says so (set `ALLOW_SHRINK=true` if that is really what you want); otherwise at most `MAX_REMOVALS_PER_ROUND` titles (25 by default) leave the collection per round, and they come back on the next round if the list recovers. A title the source still lists but Jellyfin cannot match this round (unknown id, a name that now fits two titles) is not removed because of that. A collection of the same name that this tool did not make is left completely alone unless `ADOPT=true` (the tool remembers the collection it made in the state file; deleting the state file makes it treat its own collection as foreign until `ADOPT=true` is set once).

The collection shows titles, not dates (Jellyfin has no per-title text for a collection). Dates are in `lombo-farewell plan`, and the optional alert tells you about a title that is overdue.

## Install in a minute

1. In Jellyfin, open Dashboard > API Keys and create a key for LomboFarewell.
2. Copy `.env.example` to `.env`; fill in the Jellyfin URL, the key and where Maintainerr answers (or switch to the file source, see below).
3. Make the data folder writable for the container user and start it:
   ```sh
   mkdir data && chown 999 data
   docker compose -f compose.example.yaml up -d
   docker compose -f compose.example.yaml exec lombo-farewell lombo-farewell doctor
   docker compose -f compose.example.yaml exec lombo-farewell lombo-farewell plan
   ```
4. When `plan` looks right, set `LOMBO_FAREWELL_DRY_RUN=false` and restart. The collection appears on the next round.

Setup details: [docs/setup.md](docs/setup.md).

## Commands

| Command | What it does |
|---|---|
| `lombo-farewell doctor` | Checks the settings, Jellyfin, the key, the source and the collection. Exit code 1 if something fails. |
| `lombo-farewell plan` | Shows what the next round would do. Never changes anything. |
| `lombo-farewell run [--loop]` | Does it. `--loop` repeats every `LOMBO_FAREWELL_INTERVAL_SECONDS`. With `DRY_RUN=true` it only prints. |
| `lombo-farewell keep "Title"` | Postpones one title's deletion by `KEEP_DAYS` (Maintainerr source only). With `DRY_RUN=true` it only says what it would do. |
| `lombo-farewell serve` | Optional small HTTP API so a web page or another app can read the leaving list and ask for a keep. Needs `LOMBO_FAREWELL_API_TOKEN`. See below. |

## Where the list comes from

The source is a setting, `LOMBO_FAREWELL_SOURCE`:

- **`maintainerr`** (default): reads Maintainerr's active collections. A title is deleted `deleteAfterDays` after Maintainerr added it. This source can postpone.
- **`file`**: reads a JSON or plain-text list that your own cleanup tool, a script or you write. It cannot postpone (the tool that deletes is not one this app can talk to), so edit the list instead. Format and examples: [docs/setup.md](docs/setup.md#the-file-source).

## Keeping a title longer

```sh
lombo-farewell keep "Alpha Movie"      # a unique part of the name works too
```

It asks the source to push the deletion back by `KEEP_DAYS` days and notes in the state file that it was kept, and until when. Rules carried over from the original: a title can be kept only while it is inside its normal wait, and (from the state file) at most once every `KEEP_DAYS` days, so running the command twice in a row does nothing the second time; once the extra time is nearly used up it can be kept again. The state file is what remembers the last keep: if it is deleted, the limit starts over. By default there is no viewer-facing way to run `keep`; do not wire the command to a chat bot that viewers can use. Anything unclear (unknown title, two titles that match, source down) does nothing and says why.

### The HTTP API (optional)

The original had a "Keep it longer" button on the title's page, drawn by a script in the web client and answered by a server. This app does not draw buttons, but `lombo-farewell serve` is the server half: a small standard-library HTTP server with three routes and nothing else.

- `GET /v1/leaving`: the leaving list as JSON (id, title, days left, can_keep).
- `POST /v1/keep` with `{"title": "..."}`: the same check and postpone as `lombo-farewell keep`; answers `{"ok": ..., "message": ...}`.
- `GET /v1/health`.

It is off until you set `LOMBO_FAREWELL_API_TOKEN` (at least 24 characters), listens on `127.0.0.1:8098` by default, honours `DRY_RUN`, and needs `Authorization: Bearer <token>` on every request. It sends no CORS headers: a web page must reach it through **your own reverse proxy**, which adds the token ([Caddy and nginx snippets](examples/)); never put the token in browser code. Read [docs/security.md](docs/security.md) first, in particular that the token identifies the proxy and not the viewer: whoever can open the proxied path can postpone a deletion (within the keep limits above). The routes and the file other apps can read are specified in [docs/contracts.md](docs/contracts.md).

A live round also writes `leaving.json` beside the state file: the same list, in a versioned shape, for other apps to read without calling anything. A dry run writes no files.

Making a Jellyfin favourite or tag mean "keep" was considered and left out: it would make a viewer's tap spend a deletion postponement without any confirmation.

## Settings

Everything is an environment variable with the `LOMBO_FAREWELL_` prefix, listed with defaults in [.env.example](.env.example). A bad value (including a typo in a true/false setting) stops the app at start-up and names the variable. The two API settings are `LOMBO_FAREWELL_API_TOKEN` (required by `serve`, at least 24 characters) and `LOMBO_FAREWELL_API_BIND` (default `127.0.0.1:8098`; any other address prints a warning in `doctor` and at start). A misspelled setting *name* does not stop it: the app prints a warning naming the unknown `LOMBO_FAREWELL_*` variable and uses the default.

## What is not verified

Everything here was tested against **fake servers written for the tests**, never against the real thing:

- **Never run against a real Jellyfin.** The calls (`GET /Items` with `IncludeItemTypes=BoxSet`, `ParentId`, `Ids` and `SearchTerm`; `POST /Collections`; `POST` and `DELETE /Collections/{id}/Items`) follow what the original app did with an API key, but their exact replies on current Jellyfin versions are assumed, not confirmed. In particular: whether an API key sees a collection's members the way the code expects, whether a collection made this way shows in every client, and the title search by exact name.
- **Never run against a real Maintainerr.** The three endpoints and the field names (`isActive`, `deleteAfterDays`, `mediaServerId`, `addDate`, `deletionDate`) come from the original app's code and may have changed in newer Maintainerr versions.
- The third-audit fixes (shrink guard, owned-collection check, keep limit, redirect refusal, number checks, text cleaning, run lock) are covered by tests against the fake servers only. The lock and the redirect refusal were not tried against a real proxy or a real second process; the duplicate-collection race on a first run of two processes is prevented by the lock but was not run for real.
- **The HTTP API** (`serve`) was tested only with a real socket on `127.0.0.1` and Python's own HTTP client, against the fake servers. It was never run behind a real Caddy or nginx (the two snippets in `examples/` are untested), never called from a real web page, and no Jellyfin client script uses it yet. IPv6 binds and a non-loopback bind were not tried. It has had no independent security review.
- `leaving.json` is tested for its shape, its mode and its atomic write, but no other app reads it yet.
- No collection ordering: Jellyfin has no reorder call, so titles are added soonest first and clients sort as they like.
- The container image was not built and the CI workflows were not run.

## FAQ

**Is it safe to try?** Dry run is the default and changes nothing. Even live, the only changes are to one collection.

**Does it need the LomboShelf plugin?** No. It manages a plain Jellyfin collection, which any client shows, LomboShelf included.

**Can I use another cleanup tool?** Yes: write the titles to a file (`SOURCE=file`).

**What if the tool deletes a title that is overdue?** It leaves the collection on the next round (the source no longer lists it).

**Can a button in Jellyfin call it?** The server half exists (`serve`); the button belongs in a web-client script such as the Shelf plugin, which does not call it yet.

**Why does `keep` not work with the file source?** There is nothing to tell the other tool. Change the date or remove the line in your file.

## Contributing and licence

Contribution, security and conduct rules come from the [Lombo-Forge organisation](https://github.com/Lombo-Forge/.github). Licensed under [GPL-3.0](LICENSE). Not affiliated with or endorsed by the Jellyfin project or Maintainerr.
