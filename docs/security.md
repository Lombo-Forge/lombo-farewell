# Security notes

## Contents

- [What `serve` is](#what-serve-is)
- [What it trusts](#what-it-trusts)
- [What it refuses](#what-it-refuses)
- [What it never logs](#what-it-never-logs)
- [The post-keep command](#the-post-keep-command)
- [Reaching it from a web page](#reaching-it-from-a-web-page)
- [Limits of this design](#limits-of-this-design)
- [The state files](#the-state-files)

This is the author's own threat model for the optional HTTP API (`lombo-farewell serve`). There has been no
independent review, and it was never run against a real reverse proxy or a real browser.

## What `serve` is

A small server built on Python's `http.server`, started only when you run `lombo-farewell serve`. It listens on
`LOMBO_FAREWELL_API_BIND` (default `127.0.0.1:8098`), speaks plain HTTP (no TLS: put it behind a proxy that does TLS),
and has four routes: `GET /v1/leaving`, `GET /v1/keeps`, `POST /v1/keep`, `GET /v1/health`. The only thing it can change is a keep: a
postponement through the cleanup tool, the same one `lombo-farewell keep` makes. It cannot delete anything.

## What it trusts

- **The token.** Whoever holds `LOMBO_FAREWELL_API_TOKEN` can ask for a keep. The token proves *which program* is calling, not *which person* is behind it.
- **The network position.** A loopback bind trusts only programs on the same machine (or in the same container). Any other bind trusts everything that can reach that network.
- **The reverse proxy**, if there is one: it must set the `Authorization` header itself and overwrite any value the visitor sent.
- **Maintainerr, Jellyfin and the state file**, exactly as the rest of the app does.

## What it refuses

- Starting without a token, or with one shorter than 24 characters, or with spaces or non-ASCII characters.
- Every request without the right `Authorization: Bearer` header (`401`, no detail). The token is compared in constant time.
- More than 10 failed tries a minute from one address (`429`); the limit is checked before the token is, so it slows a guesser who happens to be right too. The table of addresses is capped at 1024.
- Any path other than the four routes (`404`) and any other method (`405`), after the token is checked, so a stranger learns nothing about the routes.
- A body over 4096 bytes, a missing `Content-Length` (no chunked bodies), a content type other than `application/json`, anything but strict JSON of the shape `{"title": "..."}` with an optional `"by": "..."` (1 to 60 printable characters): duplicate keys, `NaN`, extra keys, a non-string, a title that is empty, over 200 characters or holds control characters.
- Requests that stall: a connection that does not finish its request in 10 seconds is dropped.
- More than one keep at a time: keeps wait for each other, and for a running `run` or `keep` (the shared run lock).
- To do anything in dry run: a keep says what it would do and changes nothing.
- CORS: no `Access-Control-*` header is ever sent, and a browser's preflight request (`OPTIONS`) is not answered, so a web page on another site cannot call it directly.

## What it never logs

It prints one line per request to standard error: the client address, the method, the path (cleaned, 60 characters at most) and the status. Never the `Authorization` header, never the query string, never a request body, never the token. For a keep it also logs the requested title (cleaned, 60 characters at most), which a viewer typed or a page sent. An upstream error is logged by its message (which names the failing call, not a key); an unexpected error only by its type.

## The post-keep command

`LOMBO_FAREWELL_POST_KEEP_COMMAND` is the owner's own setting, like the rest of the environment, so it is run as
given. It is split like a shell would split it but started without a shell: no pipes, no `$VAR`, no globbing. It runs
as the same user as the app, with the app's environment plus `LOMBO_FAREWELL_KEPT_JSON` (the title's Jellyfin id and
its cleaned name). Both are already visible to anyone who can call `keep`, but the title text came from Jellyfin, so
a command must never paste it into a shell line. It gets no input, its output is thrown away and it is stopped after
60 seconds. It runs only after a keep that really postponed a title (never in a dry run, never for a refusal), after
the run lock is released, so it cannot hold the lock. A failure never fails the keep. The app never logs the
command's environment, only a short note such as `post-keep command failed (exit 1)`. An unbalanced quote is a
start-up error that names the variable.

## Reaching it from a web page

Never put the token in browser code: anyone who opens the page can read it. Instead:

1. Keep `serve` on a private network, not published on the host.
2. Let your own reverse proxy expose the two routes you need under a path of its own, and have the **proxy** add the `Authorization` header. Ready snippets for Caddy and nginx are in [`examples/`](../examples/).
3. The proxy replaces any `Authorization` header the visitor sends, so a visitor cannot try tokens.

If the API is not behind a proxy that overwrites the header, a visitor can send wrong tokens, and the failure limit then locks out that address (and, behind a proxy that hides addresses, everybody). Behind a correct proxy a visitor never sends a bad token.

## Limits of this design

- **The token authenticates the proxy, not the viewer.** Once the proxy adds the token for a path, anyone who can open that path can postpone a deletion. The existing keep rules still apply (only inside the normal wait, at most once every `KEEP_DAYS` days per title), so the worst a visitor can do is postpone every title once per period. `MAX_KEEPS_PER_PERSON` and `MAX_KEEPS_PER_TITLE` (off by default) cap that further; the first needs a trustworthy `by` (next point). Put the path behind the same login as the rest of your server (your proxy's own authentication, or a network that only your household can reach), or accept that.
- **No TLS, no sessions, no per-person identity.** A keep is attributed only to the `by` name that the caller sends,
  and the app cannot check it. Your proxy or admin page must take the name from the viewer's own signed-in session
  and never from something the viewer typed; otherwise `MAX_KEEPS_PER_PERSON` can be dodged by changing the name.
  Without `by`, a keep is recorded as nobody's and is not counted for any person.
- **A non-loopback bind.** Inside a container you must bind `0.0.0.0` for another container to reach it; `doctor` and `serve` then print a warning. That is acceptable only on a private container network with no published port.
- **IPv6** binds (`[::1]:8098`) are supported by the code but were not tried.
- **The failure limit is per address**, so it cannot stop a distributed guess. A 32-character random token (`openssl rand -hex 32`) is what protects you.
- **Changing the token** means restarting `serve` and updating the proxy.
- Python's `http.server` is not built for the open internet. Never publish this port.

## The state files

`state.json` (kept titles, overdue and deleting-soon alerts, the owned collection id) is written with mode 0600. `leaving.json` and `keeps.json` are written with mode 0644 on purpose, because other apps read them (see [contracts.md](contracts.md)); they hold titles, dates and the names given as `by` (who kept what), so on a machine shared with people you do not trust, put the data folder somewhere they cannot read. Neither holds the token or the Jellyfin key. Both are replaced atomically (temporary name, flush, rename).
