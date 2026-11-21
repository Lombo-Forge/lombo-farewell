# AGENTS.md

Notes for an AI coding agent (or a person) working in this repo. `CLAUDE.md` pulls this file in.

## Contents

- [What this is](#what-this-is)
- [Layout](#layout)
- [Rules](#rules)
- [Test](#test)
- [Working efficiently](#working-efficiently)
- [Skills](#skills-claudeskills)

## What this is

LomboFarewell is a small Python 3.12 program that runs beside a Jellyfin server. It reads which titles a cleanup tool (Maintainerr, or a list file)
will delete, keeps a "Leaving soon" Jellyfin collection holding exactly those titles, and can postpone one title's deletion (`keep`).
It never deletes anything itself. Standard library only (add a dependency only when it clearly earns its place).
It has not been run against a real Jellyfin or Maintainerr; the README lists what is not verified.

## Layout

| Path | What |
|---|---|
| `src/lombo_farewell/core.py` | The decisions. Pure: no network, disk or clock (time is passed in). `plan()`, `diff()`, `find()`, `keep_check()`, `overdue_to_alert()`. |
| `src/lombo_farewell/sources.py` | The two sources: `MaintainerrSource` and `FileSource`, and the list parser. Raise `SourceError`; never return a partial list. |
| `src/lombo_farewell/jellyfin.py` | The Jellyfin client: collection calls and title lookups. |
| `src/lombo_farewell/engine.py` | Gathers entries and applies the collection change. No printing, no clock. |
| `src/lombo_farewell/httpjson.py` | One small JSON-over-HTTP helper used by both clients. |
| `src/lombo_farewell/state.py` | The JSON state file (kept titles, overdue alerts). |
| `src/lombo_farewell/config.py` | Settings from `LOMBO_FAREWELL_*` environment variables; `SETTINGS` lists them all. |
| `src/lombo_farewell/notify.py` | Webhook alerts. |
| `src/lombo_farewell/api.py` | The optional HTTP API (`serve`): routes, token check, failure limit, body limits. Only HTTP; the rules come in as two functions from `cli.py`. Never logs the token or a body. Read `docs/security.md` before changing it. |
| `src/lombo_farewell/cli.py` | `doctor`, `plan`, `run`, `keep`, `serve`. The only place that prints or reads the clock. |
| `tests/` | pytest. One fake server (Jellyfin, Maintainerr and webhook) in `conftest.py`. |
| `scripts/leak-check.sh` | Fails when private or personal strings appear. CI runs it. |
| `examples/` | Reverse-proxy snippets (Caddy, nginx) that add the API token for two routes. |
| `docs/` | One topic per file, each with a `## Contents` list. `contracts.md` and `security.md` cover the API. |

## Rules

- **Keep `core.py` pure.** If a rule needs data, pass it in (the clock is an argument).
- **Never delete.** The only Jellyfin deletes allowed are `DELETE /Collections/{id}/Items` (unlist from the collection). A test checks this.
- **Fail safe.** A source or Jellyfin that cannot be read ends the round before any write. Additions go before removals. Removals are capped per round, and held back entirely when the list shrinks under half (`ALLOW_SHRINK`). A collection this tool did not make is left alone (`ADOPT`). A title the source lists but Jellyfin cannot match is never removed for that. No empty collection is ever created. Never follow a redirect with the API key. Titles are passed through `core.clean()` before they are printed or sent.
- **Safe by default.** `DRY_RUN` defaults to true. `plan` never writes anything. `keep` does nothing in dry run, and neither does a keep over the API; a dry run writes no `leaving.json`.
- **The API and `leaving.json` are contracts.** Routes, status codes and the file shape are in `docs/contracts.md`; change them there in the same pull request, and bump `version` only as that file says. The API needs a token of 24+ characters, sends no CORS headers, and the token is never logged or put in browser code.
- **A new setting** goes in `config.py` (validated, message names the variable), `SETTINGS`, `.env.example` (a test keeps them in sync).
- **A new source** follows the contract in `docs/architecture.md` (`can_postpone`, `fetch`, `postpone`, `SourceError`).
- **Never put private details in the repo:** real hostnames, IPs, webhook URLs, keys, local paths. Use `jellyfin.example.com`, `/mnt/media`, `changeme`. `scripts/leak-check.sh` enforces it; patterns live in `scripts/leak-patterns.txt`.
- Plain language in docs and messages. Never imply the Jellyfin or Maintainerr projects made this.
- The Lombo look (every icon has the Lombo L) is in `docs/branding.md`.

## Test

```sh
pip install -e '.[dev]'
pre-commit install            # once: runs the checks below before every commit
pre-commit run --all-files    # ruff lint + format, strict mypy, file hygiene, leak check
pytest -q --cov               # tests with coverage (the gate is `fail_under` in pyproject.toml)
```

These are the same checks CI runs (one config: `.pre-commit-config.yaml` and `pyproject.toml`), so what passes
locally passes there. The tooling is the same in every Lombo-Forge repo: ruff for lint and format, strict mypy,
pytest. Do not loosen a rule to make a change pass; fix the code, or say why in a comment next to a narrow `noqa`.

Without Python installed: `docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip install -q -e '.[dev]' && pytest -q"`.
Tests use a fake server; before claiming a Jellyfin or Maintainerr behaviour works, check it against a throwaway
container, never someone's real server.

## Working efficiently

- Optional tools that live in your own profile, not in this repo. Skip any you do not have.
- Graphify: build the code-only graph with `graphify update .` (never `/graphify .`, which costs tokens) and run
  `graphify query "<question>"` before grepping. `graphify-out/` is generated and gitignored; INFERRED edges are guesses.
- Ponytail: the smallest change that fully solves the task. Keep diffs small and focused.

## Skills (`.claude/skills/`)

- `install-into-my-stack`: help a user put the app into their setup and run `doctor`.
- `troubleshoot`: read `doctor` and `plan` output and the logs.
- `adapt`: change the rules, sources or settings safely.
- `ship-a-change`, `release`: get a change into `main`; cut a release.
