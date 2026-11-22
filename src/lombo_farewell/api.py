"""The small HTTP API behind `lombo-farewell serve` (docs/contracts.md, docs/security.md).

Four routes, nothing else: GET /v1/leaving, GET /v1/keeps, POST /v1/keep, GET /v1/health. Every request needs
`Authorization: Bearer <token>`. This module only speaks HTTP; what a keep or the list *is* comes in as two
functions from cli.py, so the rules stay in one place. No CORS headers on purpose: a browser page reaches this
through the owner's own reverse proxy, which adds the token (examples/).
"""

import hmac
import json
import socket
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from . import core
from .config import Config, parse_bind

MAX_BODY = 4096  # bytes of request body
MAX_TITLE = 200  # characters
MAX_FAILURES = 10  # bad or missing tokens per client per window, then 429
WINDOW = 60.0  # seconds
MAX_CLIENTS = 1024  # failure records kept at once
TIMEOUT = 10.0  # seconds a client may stall on one request

ROUTES = {"/v1/leaving": "GET", "/v1/keeps": "GET", "/v1/keep": "POST", "/v1/health": "GET"}

Reply = tuple[int, dict[str, Any], dict[str, str] | None]  # status, JSON body, extra headers


def fail(code: int, message: str, extra: dict[str, str] | None = None) -> Reply:
    return code, {"ok": False, "message": message}, extra


Keep = Callable[[str, str | None], tuple[bool, str]]  # (title, by) -> (done, message); raises on an upstream failure
Leaving = Callable[[], dict[str, Any]]  # the leaving document (version, updated, entries)


class FailureLimiter:
    """Counts bad tries per client address. Asked before the token is compared, so guessing is slowed
    for everyone, a right guess included."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.failures: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def _recent(self, client: str, now: float) -> deque[float]:
        times = self.failures.get(client)
        if times is not None:
            while times and now - times[0] >= WINDOW:
                times.popleft()
            if not times:
                del self.failures[client]
        return times if times is not None and client in self.failures else deque()

    def blocked(self, client: str) -> bool:
        with self.lock:
            return len(self._recent(client, self.clock())) >= MAX_FAILURES

    def record(self, client: str) -> None:
        with self.lock:
            now = self.clock()
            times = self._recent(client, now)
            times.append(now)
            self.failures[client] = times
            if len(self.failures) > MAX_CLIENTS:  # the oldest client is forgotten; memory stays bounded
                del self.failures[next(iter(self.failures))]


def parse_keep_body(raw: bytes) -> tuple[str | None, str | None, str]:
    """(title, by, "") for a valid body, else (None, None, why). `by` is None when the body has no "by".

    Strict: the keys are "title" and optionally "by", both strings, no duplicates, no NaN."""

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def refuse(name: str) -> Any:
        raise ValueError(name)

    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates, parse_constant=refuse)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None, None, "the body must be strict JSON"
    if (
        not isinstance(data, dict)
        or not {"title"} <= set(data) <= {"title", "by"}
        or not all(isinstance(v, str) for v in data.values())
    ):
        return None, None, 'the body must be {"title": "..."} with an optional "by": "..."'
    title = data["title"].strip()
    if not title or len(title) > MAX_TITLE or not title.isprintable():
        return None, None, f"the title must be 1 to {MAX_TITLE} printable characters"
    by = data.get("by")
    if by is not None and not core.valid_by(by):
        return None, None, f"by must be 1 to {core.MAX_BY} printable characters with no spaces at either end"
    return title, by, ""


def make_server(
    cfg: Config,
    keep: Keep,
    leaving: Leaving,
    bind: tuple[str, int] | None = None,
    clock: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] | None = None,
    after_keep: Callable[[], str | None] | None = None,
    keeps: Leaving | None = None,
) -> ThreadingHTTPServer:
    """Build the server (not started). `bind` overrides API_BIND (tests use port 0).

    `after_keep` runs after a keep that went through, once the keep lock is released (so a slow command cannot
    hold it). If it returns a short note, the note is added to the answer's message. `keeps` returns the keep
    history document for GET /v1/keeps (an empty one when not given)."""
    token = cfg.api_token.encode()
    limiter = FailureLimiter(clock)
    one_keep = threading.Lock()  # one keep at a time, on top of the run lock shared with `run` and `keep`

    def say(text: str) -> None:
        (log or (lambda t: print(t, file=sys.stderr, flush=True)))(text)

    class Handler(BaseHTTPRequestHandler):
        server_version = "lombo-farewell"
        sys_version = ""
        timeout = TIMEOUT

        def log_message(self, format: str, *args: Any) -> None:  # silence the default access log
            pass

        def _send(self, code: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)
            route = core.clean(urlsplit(self.path).path)[:60]  # never the headers, the query or the body
            say(f"[api] {self.client_address[0]} {self.command} {route} -> {code}")

        def _authorised(self) -> bool:
            client = self.client_address[0]
            if limiter.blocked(client):
                self._send(*fail(429, "too many failed tries; wait a minute", {"Retry-After": str(int(WINDOW))}))
                return False
            scheme, _, given = (self.headers.get("Authorization") or "").partition(" ")
            if scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), token):
                return True
            limiter.record(client)
            self._send(*fail(401, "unauthorized", {"WWW-Authenticate": "Bearer"}))
            return False

        def _dispatch(self) -> None:
            if self._authorised():
                self._send(*self._handle(urlsplit(self.path).path))

        def _handle(self, route: str) -> Reply:
            allowed = ROUTES.get(route)
            if allowed is None:
                return fail(404, "not found")
            if self.command != allowed:
                return fail(405, f"use {allowed} here", {"Allow": allowed})
            try:
                if route == "/v1/health":
                    return 200, {"ok": True}, None
                if route == "/v1/leaving":
                    return 200, leaving(), None
                if route == "/v1/keeps":
                    return 200, (keeps() if keeps else {"version": 1, "keeps": []}), None
                return self._keep()
            except Exception as e:  # noqa: BLE001  # one bad request must never stop the server
                known = type(e).__module__.startswith("lombo_farewell")  # our own errors have readable messages
                say(f"[FAIL] api {route}: {e if known else type(e).__name__}")
                if known:
                    return fail(502, "could not complete the request; see the log")
                return fail(500, "error")

        def _keep(self) -> Reply:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            length_text = self.headers.get("Content-Length")
            if ctype != "application/json":
                return fail(415, "send Content-Type: application/json")
            if length_text is None or not length_text.isdigit():
                return fail(411, "send a Content-Length")
            if int(length_text) > MAX_BODY:
                return fail(413, f"the body may be at most {MAX_BODY} bytes")
            title, by, why = parse_keep_body(self.rfile.read(int(length_text)))
            if title is None:
                return fail(400, why)
            say(f"[api] keep requested for {core.clean(title)[:60]!r}")
            with one_keep:
                done, message = keep(title, by)
            if done and after_keep and (note := after_keep()):
                message = f"{message} Note: {note}."
            return (200 if done else 422), {"ok": done, "message": message}, None

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _dispatch

    host, port = bind or parse_bind(cfg.api_bind)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    return Server((host, port), Handler)
