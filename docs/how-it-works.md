# How it works

## Contents

- [One round](#one-round)
- [How a deletion date is worked out](#how-a-deletion-date-is-worked-out)
- [How the collection is changed](#how-the-collection-is-changed)
- [Keep it longer](#keep-it-longer)
- [What is remembered](#what-is-remembered)
- [What it never does](#what-it-never-does)

## One round

1. Read the source (Maintainerr or the list file). If that fails, the round ends here and nothing changes.
2. Look the titles up in Jellyfin (names by id, or ids by exact name). Titles Jellyfin does not know are skipped and named in the output.
3. Work out each title's days left, and sort soonest first. A title in two Maintainerr groups is listed once, with the sooner date.
4. Compare with the collection: add what is missing, then remove what is stale (at most `MAX_REMOVALS_PER_ROUND`).
5. With `DRY_RUN=false`: send an alert for any title 2 or more days overdue (once), and, with `ALERT_DELETING_SOON=true`, one for any title within a day of deletion (once); forget `kept` notes for titles that left the list; save the state file and `leaving.json`.

## How a deletion date is worked out

Maintainerr deletes a title `deleteAfterDays` after it was added to one of its collections, so the date is `addDate + deleteAfterDays`. `days left` rounds up; a title past its date shows as overdue by whole days (the cleanup tool should have deleted it already). The list file gives its own date (`leaves`), or none.

## How the collection is changed

The collection (`COLLECTION_NAME`, locked, made on the first live run) should hold exactly the listed movies and shows.

- Nothing scheduled and no collection yet: no collection is made.
- Titles are added in groups of 50, soonest first.
- Stale titles are then removed, in groups of 50, but never more than `MAX_REMOVALS_PER_ROUND` in a round. The cap alone only slows an empty list down (60 titles would be gone in three hourly rounds), so there are two more guards:
  - **Shrink guard:** when the wanted list is empty, or under half of what the collection holds, nothing is removed and a warning is printed. `ALLOW_SHRINK=true` switches this off (the cap still applies).
  - **Unmatched titles stay:** a title the source lists but Jellyfin cannot match this round (an id it does not know, a name that now fits two titles) counts as still wanted for the removal step.
- A collection that exists but is not the one this tool made (its id is not in the state file as `owned`) is left completely alone: no adds, no removals, a warning. `ADOPT=true` lets the tool take it over; the id is then remembered.
- Only movies and shows are looked at, not seasons or episodes.

## Keep it longer

`lombo-farewell keep "Title"`:

1. Reads the source and finds the one title that matches (exact name or Jellyfin id first, then a unique part of a name).
2. Refuses if nothing or more than one title matches, or if the title is already kept (its date is beyond the normal wait, or it was kept less than `KEEP_DAYS` days ago according to the state file).
3. Refuses if a limit is over: `MAX_KEEPS_PER_TITLE` (counted in the state file) or `MAX_KEEPS_PER_PERSON` (counted from `keeps.json`, by name, ignoring case, over the last `KEEP_DAYS` days; it also refuses a keep with no name). Both are off by default.
4. With `DRY_RUN=true`, says what it would do and stops.
5. Otherwise asks Maintainerr to postpone that title by `KEEP_DAYS` days, writes down the new date, who kept it and how many times, adds a row to `keeps.json` and rewrites `leaving.json` so a reader sees the keep at once. A failure to write the last two is a warning only.
6. Releases the run lock, then sends the keep alert (`ALERT_ON_KEEP`) and, if `POST_KEEP_COMMAND` is set, runs it once (60 seconds at most). A slow command
   therefore never holds up a round or another keep. If it fails, the keep still counts and a short note says so.

The title stays in the collection until it is really gone; Jellyfin shows no per-title date, so check `plan`.

## What is remembered

The state file (`LOMBO_FAREWELL_STATE_FILE`, JSON) holds the titles that were kept (name, when, until, who, how many times) and the ids already alerted as overdue or as deleting soon. Deleting it can repeat an alert, forget a keep note (and so the per-title count) and nothing else.

A live round also writes `leaving.json` next to it: the leaving list for other apps to read. It is a view, rebuilt every round and after every live keep, and nothing reads it back. `keeps.json` is the keep history (newest 500). Their shapes are in [contracts.md](contracts.md).

## What it never does

Delete a title or a file; change the cleanup tool's rules; create an empty collection; empty the collection in one round because a source misbehaved; or change anything in dry run. It runs no server unless you start `lombo-farewell serve` yourself (a separate, optional process: [security.md](security.md)).
