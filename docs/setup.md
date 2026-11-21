# Setup

## Contents

- [What you need](#what-you-need)
- [Install](#install)
- [Without Docker](#without-docker)
- [The file source](#the-file-source)
- [Making Maintainerr reachable](#making-maintainerr-reachable)
- [Going live](#going-live)

## What you need

- A Jellyfin server and an API key (Dashboard > API Keys).
- Either Maintainerr (the app reads its HTTP interface; Maintainerr has no login of its own, so keep it on a private network), or any tool or script that can write a list file.

## Install

1. `cp .env.example .env` and fill in the Jellyfin URL and key, plus `LOMBO_FAREWELL_MAINTAINERR_URL` or the file source settings.
2. `mkdir data && chown 999 data`
3. `docker compose -f compose.example.yaml up -d`
4. `docker compose -f compose.example.yaml exec lombo-farewell lombo-farewell doctor`

If Jellyfin and Maintainerr live in another compose project, join their network (see the comments in `compose.example.yaml`) and use the container names in the URLs.

## Without Docker

Python 3.12 or newer: `pip install .`, export the settings from `.env`, run `lombo-farewell run --loop`, or call `lombo-farewell run` from a timer or cron once an hour. Set `LOMBO_FAREWELL_STATE_FILE` to a writable path.

## The file source

Set `LOMBO_FAREWELL_SOURCE=file` and `LOMBO_FAREWELL_SOURCE_FILE=/data/leaving.txt` (the file must be inside a folder the container can read). The file is read on every round, so another tool can rewrite it at any time. Write it in one step (to a temporary name, then rename) so a half-written file is never read; a file that cannot be understood stops the round and changes nothing.

Plain text, one title per line, an optional date after a bar, `#` for comments:

```text
# title | date it leaves (optional)
Alpha Movie | 2026-12-01
Beta Show
```

JSON, a list (or an object with an `items` list); each entry has a `title` or a Jellyfin `id`, and optionally `leaves`:

```json
[
  {"title": "Alpha Movie", "leaves": "2026-12-01"},
  {"id": "0123456789abcdef0123456789abcdef"}
]
```

Titles are matched to movies and shows by exact name (case does not matter). A name that matches nothing, or more than one title, is skipped and named in the output: use the Jellyfin `id` for those. Dates are `YYYY-MM-DD` (midnight UTC), a full ISO time, or a Unix time.

## Making Maintainerr reachable

Check from the container: `docker compose exec lombo-farewell python -c "import urllib.request as u; print(u.urlopen('http://maintainerr:6246/api/collections').status)"`. `doctor` also reports it as "source readable".

## Going live

Read `plan` for a few rounds. When the titles and the collection changes are what you expect, set `LOMBO_FAREWELL_DRY_RUN=false` and restart. To stop, set it back to true or stop the container: Jellyfin and the cleanup tool do not depend on LomboFarewell. The collection stays; delete it in Jellyfin if you no longer want it.
