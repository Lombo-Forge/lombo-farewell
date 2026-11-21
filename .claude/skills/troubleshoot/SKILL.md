---
name: troubleshoot
description: Diagnose LomboFarewell problems from doctor and plan output and the logs. Use when the app fails to start, doctor shows FAIL, the Leaving soon collection is missing or wrong, a title is skipped, or keep does nothing.
---

# Troubleshoot LomboFarewell

Start with `lombo-farewell doctor`; each line says what failed. `docs/troubleshooting.md` has the table of messages and fixes; read it rather than
guessing. Then `lombo-farewell plan` prints every title with days left, the titles it skipped (and why) and the collection changes it would make, without
changing anything.

Rules of thumb:

- "The collection does not appear": it is made only on a live round (`DRY_RUN=false`) and only when at least one title is scheduled. Check both.
- A broken source (Maintainerr down, list file missing or with a bad line) leaves the collection exactly as it was by design. Fix the source; do not
  work around it.
- "Titles disappeared from the collection": check `MAX_REMOVALS_PER_ROUND` and `plan` ("leave in place for now"). Many removals at once usually mean the
  source reported too little.
- `skipped: ... no title / N titles with exactly this name`: file source; give the Jellyfin `id` instead of the name.
- `keep` refusals are deliberate: file source (cannot postpone), unknown or ambiguous name, already kept, source down, dry run.
- The state file holds only kept notes and overdue-alert memory; deleting it can repeat one alert and nothing else.
- The app never deletes titles. If a title was deleted, the cleanup tool did it; look at that tool's logs.
- Never ask the user to turn off dry run "to see if it works"; `plan` shows the same decisions.
- The app has never been run against a real Jellyfin or Maintainerr; if an API reply looks different from what the code expects, say so and ask for
  `doctor` output with names and keys removed.
