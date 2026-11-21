"""A fake Jellyfin, a fake Maintainerr and a fake webhook on one localhost server, so tests need no real service."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

NOW = 1_800_000_000.0  # a fixed "now" for the pure tests
DAY = 86400.0


def iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat()


def aid(n: int) -> str:
    """A 32-character Jellyfin-style id."""
    return f"{n:032x}"


class Fake:
    def __init__(self):
        self.requests: list[dict] = []
        self.key = "good-key"
        self.items = {aid(1): "Alpha Movie", aid(2): "Beta Show", aid(3): "Gamma Film", aid(4): "Gamma Film"}
        self.collections: dict[str, dict] = {}  # id -> {"Name", "members": [ids]}
        self.fail_collections_write = False
        self.garbage = False
        # Maintainerr
        self.maint_collections = [{"id": 7, "isActive": True, "deleteAfterDays": 30}]
        self.maint_media: dict[int, list[dict]] = {7: []}
        self.maint_status = 200
        self.postponed: list[dict] = []
        self.postpone_reply: dict | None = {"deletionDate": "2027-01-15T00:00:00Z"}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, payload=None, raw=None):
                data = raw if raw is not None else (b"" if payload is None else json.dumps(payload).encode())
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _log(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                outer.requests.append(
                    {"method": method, "path": u.path, "q": q, "auth": self.headers.get("Authorization"), "body": body}
                )
                return u.path, q, body

            def _authed(self):
                return self.headers.get("Authorization") == f'MediaBrowser Token="{outer.key}"'

            def do_GET(self):
                path, q, _ = self._log("GET")
                if path == "/System/Info/Public" and outer.garbage:
                    return self._reply(200, raw=b"<html>login</html>")
                if path == "/System/Info/Public":
                    return self._reply(200, {"ServerName": "Fake", "Version": "0.0.0"})
                if path.startswith("/api/collections"):
                    if outer.maint_status != 200:
                        return self._reply(outer.maint_status)
                    if path == "/api/collections":
                        return self._reply(200, outer.maint_collections)
                    return self._reply(200, outer.maint_media.get(int(q["collectionId"]), []))
                if not self._authed():
                    return self._reply(401)
                if path == "/System/Info":
                    return self._reply(200, {"ServerName": "Fake"})
                if path == "/Items":
                    return self._reply(200, {"Items": outer.query_items(q)})
                return self._reply(404)

            def do_POST(self):
                path, q, body = self._log("POST")
                if path == "/hook":
                    return self._reply(204)
                if path == "/api/collections/media/postpone":
                    outer.postponed.append(json.loads(body))
                    if outer.postpone_reply is None:
                        return self._reply(200, raw=b"{}")
                    return self._reply(200, outer.postpone_reply)
                if not self._authed():
                    return self._reply(401)
                if outer.fail_collections_write and path.startswith("/Collections"):
                    return self._reply(500)
                if path == "/Collections":
                    cid = f"c{len(outer.collections) + 1}"
                    ids = [i for i in q.get("Ids", "").split(",") if i]
                    outer.collections[cid] = {"Name": q["Name"], "members": ids}
                    return self._reply(200, {"Id": cid})
                if path.startswith("/Collections/"):
                    cid = path.split("/")[2]
                    outer.collections[cid]["members"] += q["ids"].split(",")
                    return self._reply(204)
                return self._reply(404)

            def do_DELETE(self):
                path, q, _ = self._log("DELETE")
                if not self._authed():
                    return self._reply(401)
                if path.startswith("/Collections/"):
                    cid = path.split("/")[2]
                    gone = set(q["ids"].split(","))
                    outer.collections[cid]["members"] = [m for m in outer.collections[cid]["members"] if m not in gone]
                    return self._reply(204)
                return self._reply(404)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def query_items(self, q):
        if q.get("IncludeItemTypes") == "BoxSet":
            term = q.get("SearchTerm", "").lower()
            return [{"Id": c, "Name": v["Name"]} for c, v in self.collections.items() if term in v["Name"].lower()]
        if "ParentId" in q:
            return [{"Id": m, "Name": self.items.get(m, "?")} for m in self.collections[q["ParentId"]]["members"]]
        if "Ids" in q:
            return [{"Id": i, "Name": self.items[i]} for i in q["Ids"].split(",") if i in self.items]
        term = q.get("SearchTerm", "").lower()
        return [{"Id": i, "Name": n} for i, n in self.items.items() if term in n.lower()]

    def sent(self, method):
        return [r for r in self.requests if r["method"] == method]

    def writes(self):
        return [r for r in self.requests if r["method"] in {"POST", "DELETE"} and r["path"] != "/hook"]

    def schedule(self, *entries):
        """Maintainerr titles: (jellyfin id, days since added)."""
        self.maint_media[7] = [{"mediaServerId": i, "addDate": iso(_now() - d * DAY)} for i, d in entries]

    def members(self, name="Leaving soon"):
        for v in self.collections.values():
            if v["Name"] == name:
                return v["members"]
        return None


def _now():
    import time

    return time.time()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.server.shutdown()


@pytest.fixture
def env(fake, tmp_path):
    return {
        "LOMBO_FAREWELL_JELLYFIN_URL": fake.url,
        "LOMBO_FAREWELL_JELLYFIN_API_KEY": "good-key",
        "LOMBO_FAREWELL_WEBHOOK_URL": fake.url + "/hook",
        "LOMBO_FAREWELL_MAINTAINERR_URL": fake.url,
        "LOMBO_FAREWELL_STATE_FILE": str(tmp_path / "state.json"),
    }
