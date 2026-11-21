---
name: adapt
description: Change LomboFarewell's rules, sources, settings or alerts without breaking it. Use when the user wants different behaviour, a new source, a new setting or a different alert target.
---

# Adapt LomboFarewell

Read `AGENTS.md` and `docs/architecture.md` first (the module contracts).

- **A rule** (how days left are counted, what can be kept, what is alerted) lives in `core.py`. Keep it pure: pass in whatever it needs, including the
  clock. Add the test first.
- **A new source** (another cleanup tool): a class in `sources.py` with `can_postpone`, `fetch()` and `postpone()`; raise `SourceError` for anything odd and never
  return a partial list; wire it into `engine.make_source` and `config.py` (`SOURCES`, plus the settings it needs); test it against a fake.
- **A new setting**: `config.py` (validated, message names the variable), `SETTINGS`, `.env.example`; a test keeps the three in step.
  True/false settings go through `_truthy` so a typo is an error.
- **A new Jellyfin call**: a method on `Jellyfin` in `jellyfin.py`, the path in the fake in `tests/conftest.py`, a test. Never add a call that deletes titles or files.
- Do not weaken the safety rules: dry run by default, a failed read ends the round before any write, additions before removals, the removal cap, no empty collection.
  If a change touches one, update `docs/how-it-works.md` too.
- Alerts: `notify.send` posts to any webhook; bridges handle other services.
- Finish with the checks in `ship-a-change` and the leak check.
