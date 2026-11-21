---
name: install-into-my-stack
description: Help a user install LomboFarewell next to their own Jellyfin server and verify it with doctor. Use when someone asks to set it up, install it, or add it to their compose file or server.
---

# Install LomboFarewell into the user's stack

Ask, one short question at a time, then write the config:

1. How do you run Jellyfin (Docker compose, another way) and what URL can the container reach it on?
2. Where does the "scheduled for deletion" list come from: Maintainerr (ask for its URL as seen from the container) or another tool or script (use the file source)?
3. Do you want alerts (a webhook URL that accepts JSON)? Optional.

Then:

- Create `.env` from `.env.example`. The user creates the Jellyfin API key (Dashboard > API Keys); never ask them to paste a real key into the chat.
- For the file source, explain the format in `docs/setup.md#the-file-source` and that the file should be written in one step (temporary name, then rename).
- Add the service block from `compose.example.yaml` to their compose file, with a writable `/data` folder (`mkdir data && chown 999 data`) and the right network.
- Run `lombo-farewell doctor`, fix every `[FAIL]`, then `lombo-farewell plan`. Leave `DRY_RUN=true` until the user has read `plan` and agrees.
- Going live (`DRY_RUN=false`) is the user's decision. Remind them: the app only fills one collection and can postpone a deletion; it never deletes.
- Say plainly that the app has not been tested against a real Jellyfin or Maintainerr yet.
