"""`serve` and the state file: a real socket on 127.0.0.1 (port 0) against the fake Jellyfin and Maintainerr."""
# Several checks per assertion keep the status/body pairs readable; 0.0.0.0 appears only as a test value.
# ruff: noqa: PT018, S104

import http.client
import json
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from conftest import aid
from lombo_farewell import api, cli, core, state
from lombo_farewell.cli import main
from lombo_farewell.config import ConfigError, from_env, is_loopback, parse_bind, token_problem

TOKEN = "t" * 32
LIVE = {"LOMBO_FAREWELL_DRY_RUN": "false"}


class Client:
    def __init__(self, port: int, logs: list[str]):
        self.port, self.logs = port, logs

    def call(self, method, path, body=None, token=TOKEN, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        if token is not None:
            hdrs.setdefault("Authorization", f"Bearer {token}")
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif raw is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        text = resp.read()
        out = json.loads(text) if text else None
        got = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, got, out


def legacy(entry):
    """The four fields every reader of version 1 has had from the start."""
    return {k: entry[k] for k in ("id", "title", "days_left", "can_keep")}


@pytest.fixture
def serve(env, fake):
    """Start the API the way `serve` does. Returns start(extra_env, clock, keep) -> Client."""
    servers = []

    def start(extra=None, clock=time.monotonic, keep=None):
        cfg = from_env({**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN, **(extra or {})})
        logs: list[str] = []

        def real_keep(title, by=None):
            with state.locked(cfg.state_file):
                return cli.keep_title(cfg, title, time.time(), by)

        server = api.make_server(
            cfg,
            keep or real_keep,
            lambda: cli.leaving_now(cfg, time.time()),
            bind=("127.0.0.1", 0),
            clock=clock,
            log=logs.append,
            keeps=lambda: state.keeps_document(state.load_keeps(cfg.state_file)),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return Client(server.server_address[1], logs)

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


# ---------------------------------------------------------------- settings and start-up
def test_bind_and_token_rules():
    assert parse_bind("127.0.0.1:8098") == ("127.0.0.1", 8098)
    assert parse_bind("[::1]:80") == ("::1", 80)
    for bad in ("8098", "host:", ":80", "h:0", "h:70000", "h:x"):
        with pytest.raises(ConfigError, match="API_BIND"):
            parse_bind(bad)
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.0.2.5") and not is_loopback("proxy")
    assert "not set" in (token_problem("") or "")
    assert "too short" in (token_problem("x" * 23) or "")
    assert "printable" in (token_problem("x" * 23 + " ") or token_problem("é" * 30) or "")
    assert token_problem("x" * 24) is None


def test_a_bad_bind_stops_the_app_at_start_up(env, capsys):
    assert main(["plan"], {**env, "LOMBO_FAREWELL_API_BIND": "nope"}) == 2
    assert "LOMBO_FAREWELL_API_BIND" in capsys.readouterr().err


@pytest.mark.parametrize("token", ["", "short", "x" * 23])
def test_serve_refuses_a_missing_or_short_token(env, capsys, token):
    assert main(["serve"], {**env, "LOMBO_FAREWELL_API_TOKEN": token}) == 2
    assert "LOMBO_FAREWELL_API_TOKEN" in capsys.readouterr().err


def test_serve_warns_on_a_non_loopback_bind_and_starts(env, capsys, monkeypatch):
    started = []

    class Stub:
        def serve_forever(self):
            started.append(True)

        def server_close(self):
            started.append("closed")

    monkeypatch.setattr(api, "make_server", lambda *a, **k: Stub())
    good = {**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN}
    assert main(["serve"], {**good, "LOMBO_FAREWELL_API_BIND": "0.0.0.0:8098"}) == 0
    captured = capsys.readouterr()
    assert "[WARN]" in captured.err and "not a loopback" in captured.err
    assert "DRY" not in captured.out and "dry run" in captured.out
    assert started == [True, "closed"]
    assert main(["serve"], good) == 0
    assert "[WARN]" not in capsys.readouterr().err
    assert TOKEN not in captured.out + captured.err


def test_serve_stops_cleanly_on_ctrl_c(env, monkeypatch):
    closed = []

    class Stub:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            closed.append(True)

    monkeypatch.setattr(api, "make_server", lambda *a, **k: Stub())
    assert main(["serve"], {**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN}) == 0
    assert closed == [True]


def test_doctor_reports_the_api(env, fake, capsys):
    assert main(["doctor"], env) == 0
    assert "API: off" in capsys.readouterr().out
    assert main(["doctor"], {**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN}) == 0
    out = capsys.readouterr().out
    assert "`serve` would listen on 127.0.0.1:8098" in out and "[warn]" not in out
    assert main(["doctor"], {**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN, "LOMBO_FAREWELL_API_BIND": "0.0.0.0:8098"}) == 0
    assert "[warn] API bind" in capsys.readouterr().out
    assert main(["doctor"], {**env, "LOMBO_FAREWELL_API_TOKEN": "short"}) == 1
    out = capsys.readouterr().out
    assert "[FAIL] API token" in out and "short" not in out.replace("too short", "")


# ---------------------------------------------------------------- auth, routes, limits
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + TOKEN}, {"Authorization": TOKEN}],
)
def test_wrong_or_missing_token_is_401_with_no_detail(serve, headers):
    c = serve()
    status, got, body = c.call("GET", "/v1/health", token=None, headers=headers)
    assert status == 401 and body == {"ok": False, "message": "unauthorized"}
    assert got["www-authenticate"] == "Bearer"


def test_health_and_the_right_token_work(serve):
    c = serve()
    status, got, body = c.call("GET", "/v1/health")
    assert (status, body) == (200, {"ok": True})
    assert got["content-type"] == "application/json" and got["cache-control"] == "no-store"
    assert not [h for h in got if h.startswith("access-control")]  # no CORS, ever
    assert c.call("GET", "/v1/health", headers={"Authorization": f"bearer {TOKEN}"})[0] == 200


def test_unknown_routes_404_and_wrong_methods_405_only_after_auth(serve):
    c = serve()
    assert c.call("GET", "/", token=None)[0] == 401  # a stranger learns nothing about the routes
    assert c.call("GET", "/nope")[0] == 404
    assert c.call("GET", "/v1/leaving/extra")[0] == 404
    status, got, _ = c.call("POST", "/v1/health", body={})
    assert status == 405 and got["allow"] == "GET"
    status, got, _ = c.call("GET", "/v1/keep")
    assert status == 405 and got["allow"] == "POST"
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert c.call(method, "/v1/leaving")[0] == 405
    assert c.call("OPTIONS", "/v1/keep", token=None)[0] == 401  # a CORS preflight is not answered


def test_ten_bad_tries_then_429_even_for_the_right_token_until_the_minute_is_up(serve):
    now = [1000.0]
    c = serve(clock=lambda: now[0])
    for _ in range(api.MAX_FAILURES):
        assert c.call("GET", "/v1/health", token="wrong")[0] == 401
    status, got, body = c.call("GET", "/v1/health", token="wrong")
    assert status == 429 and got["retry-after"] == "60" and body["ok"] is False
    assert c.call("GET", "/v1/health")[0] == 429
    now[0] += api.WINDOW + 1
    assert c.call("GET", "/v1/health")[0] == 200


def test_good_requests_do_not_count_as_failures(serve):
    c = serve()
    for _ in range(api.MAX_FAILURES * 2):
        assert c.call("GET", "/v1/health")[0] == 200


def test_the_failure_table_stays_small():
    lim = api.FailureLimiter(clock=lambda: 0.0)
    for i in range(api.MAX_CLIENTS + 50):
        lim.record(f"10.0.{i // 250}.{i % 250}")
    assert len(lim.failures) == api.MAX_CLIENTS


# ---------------------------------------------------------------- request body rules
def test_keep_body_rules(serve, fake):
    fake.schedule((aid(1), 29))
    c = serve(LIVE)
    ok = {"Content-Type": "application/json"}
    assert c.call("POST", "/v1/keep", body={"title": "x"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert c.call("POST", "/v1/keep", raw=b"{}", headers={"Content-Type": "application/json; charset=utf-8"})[0] == 400
    assert c.call("POST", "/v1/keep", raw=b"x" * (api.MAX_BODY + 1), headers=ok)[0] == 413
    bad = [
        b"not json",
        b"",
        b"[]",
        b'"title"',
        b'{"title": 5}',
        b'{"title": null}',
        b'{"title": "a", "extra": 1}',
        b'{"title": "a", "title": "b"}',
        b'{"title": NaN}',
        b'{"title": "   "}',
        b'{"title": "bad\\u0000char"}',
        b'{"title": "bad\\nline"}',
        b'{"title": "\xff"}',
        b'{"title": "' + b"a" * (api.MAX_TITLE + 1) + b'"}',
        b"[" * 3000,  # deep nesting must be refused, not crash the handler
    ]
    for raw in bad:
        assert c.call("POST", "/v1/keep", raw=raw)[0] == 400, raw[:30]
    assert fake.postponed == []  # nothing got through to the source


def test_a_keep_without_a_length_is_411(serve):
    c = serve()
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=10)
    conn.putrequest("POST", "/v1/keep")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()
    assert conn.getresponse().status == 411
    conn.close()


# ---------------------------------------------------------------- the leaving list
def test_leaving_returns_the_plan_soonest_first(serve, fake):
    fake.schedule((aid(2), -5), (aid(1), 29))  # Beta is 35 days out, beyond the 30-day wait
    status, _, body = serve().call("GET", "/v1/leaving")
    assert status == 200
    assert body["version"] == 1 and isinstance(body["updated"], int)
    assert [legacy(e) for e in body["entries"]] == [
        {"id": aid(1), "title": "Alpha Movie", "days_left": 1, "can_keep": True},
        {"id": aid(2), "title": "Beta Show", "days_left": 35, "can_keep": False},
    ]


def test_leaving_says_can_keep_false_for_a_title_kept_lately(serve, fake, env):
    fake.schedule((aid(1), 29))
    state.save(env["LOMBO_FAREWELL_STATE_FILE"], {**state.empty(), "kept": {aid(1): {"at": time.time()}}})
    entries = serve().call("GET", "/v1/leaving")[2]["entries"]
    assert entries[0]["can_keep"] is False


def test_leaving_with_the_source_down_is_502_and_hides_the_detail(serve, fake):
    fake.maint_status = 500
    status, _, body = serve().call("GET", "/v1/leaving")
    assert status == 502 and body == {"ok": False, "message": "could not complete the request; see the log"}


# ---------------------------------------------------------------- keep
def test_keep_in_dry_run_changes_nothing(serve, fake, env):
    fake.schedule((aid(1), 29))
    status, _, body = serve().call("POST", "/v1/keep", body={"title": "alpha"})
    assert status == 200 and body["ok"] is True and body["message"].startswith("Dry run: would keep Alpha Movie")
    assert fake.postponed == [] and not Path(env["LOMBO_FAREWELL_STATE_FILE"]).exists()


def test_a_live_keep_works_once_and_a_second_is_refused(serve, fake, env):
    fake.schedule((aid(1), 29))
    c = serve(LIVE)
    status, _, body = c.call("POST", "/v1/keep", body={"title": "Alpha Movie"})
    assert status == 200 and body["ok"] is True and "it now leaves on 2027-01-15" in body["message"]
    assert fake.postponed == [{"collectionId": 7, "mediaId": aid(1), "days": 30}]
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]["title"] == "Alpha Movie"
    status, _, body = c.call("POST", "/v1/keep", body={"title": "Alpha Movie"})
    assert status == 422 and body["ok"] is False and "already kept" in body["message"]
    assert len(fake.postponed) == 1


@pytest.mark.parametrize("title", ["zzz", "a"])
def test_a_refused_keep_is_422_and_changes_nothing(serve, fake, title):
    fake.schedule((aid(1), 29), (aid(2), 29))
    status, _, body = serve(LIVE).call("POST", "/v1/keep", body={"title": title})
    assert status == 422 and body["ok"] is False
    assert fake.postponed == []


def test_a_keep_with_the_file_source_is_refused(serve, env, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("Alpha Movie\n")
    extra = {**LIVE, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": str(listing)}
    status, _, body = serve(extra).call("POST", "/v1/keep", body={"title": "Alpha Movie"})
    assert status == 422 and "cannot postpone" in body["message"]


def test_a_failed_postpone_is_502_and_records_nothing(serve, fake, env):
    fake.schedule((aid(1), 29))
    fake.postpone_reply = None
    status, _, body = serve(LIVE).call("POST", "/v1/keep", body={"title": "Alpha Movie"})
    assert status == 502 and body["ok"] is False
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"] == {}


def test_a_surprise_error_is_500_with_no_detail_and_the_server_lives_on(serve):
    def boom(title, by):
        raise RuntimeError("secret detail " + TOKEN)

    c = serve(keep=boom)
    status, _, body = c.call("POST", "/v1/keep", body={"title": "x"})
    assert status == 500 and body == {"ok": False, "message": "error"}
    assert TOKEN not in " ".join(c.logs) and "RuntimeError" in " ".join(c.logs)
    assert c.call("GET", "/v1/health")[0] == 200


def test_keeps_are_served_one_at_a_time(serve):
    inside, worst, lock = [0], [0], threading.Lock()

    def slow(title, by):
        with lock:
            inside[0] += 1
            worst[0] = max(worst[0], inside[0])
        time.sleep(0.15)
        with lock:
            inside[0] -= 1
        return True, "ok"

    c = serve(keep=slow)
    threads = [threading.Thread(target=c.call, args=("POST", "/v1/keep", {"title": f"t{i}"})) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert worst[0] == 1


def test_the_log_never_holds_the_token_or_a_whole_body(serve):
    c = serve(keep=lambda title, by: (True, "ok"))
    c.call("GET", "/v1/health")
    c.call("GET", "/v1/health", token="guess-" + "g" * 30)
    c.call("POST", "/v1/keep", body={"title": "Some Title"})
    c.call("GET", "/v1/leaving?token=" + TOKEN, token="guess")
    log = "\n".join(c.logs)
    assert TOKEN not in log and "guess" not in log and "Authorization" not in log
    assert "GET /v1/health -> 200" in log and "-> 401" in log
    assert '{"title"' not in log


# ---------------------------------------------------------------- leaving.json
def test_a_live_round_writes_the_versioned_state_file_atomically(env, fake):
    fake.schedule((aid(2), -5), (aid(1), 29))
    assert main(["run"], {**env, **LIVE}) == 0
    path = Path(env["LOMBO_FAREWELL_STATE_FILE"]).parent / "leaving.json"
    doc = json.loads(path.read_text())
    assert list(doc) == ["version", "updated", "entries"]
    assert doc["version"] == 1 and isinstance(doc["updated"], int) and abs(doc["updated"] - time.time()) < 60
    assert [legacy(e) for e in doc["entries"]] == [
        {"id": aid(1), "title": "Alpha Movie", "days_left": 1, "can_keep": True},
        {"id": aid(2), "title": "Beta Show", "days_left": 35, "can_keep": False},
    ]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_dry_run_writes_no_state_file(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["run"], env) == 0
    assert not (Path(env["LOMBO_FAREWELL_STATE_FILE"]).parent / "leaving.json").exists()


def test_an_empty_list_is_written_as_empty(env, fake):
    assert main(["run"], {**env, **LIVE}) == 0
    path = Path(env["LOMBO_FAREWELL_STATE_FILE"]).parent / "leaving.json"
    assert json.loads(path.read_text())["entries"] == []


def test_the_file_follows_the_round_after_a_keep(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 0
    assert main(["run"], {**env, **LIVE}) == 0
    path = Path(env["LOMBO_FAREWELL_STATE_FILE"]).parent / "leaving.json"
    assert json.loads(path.read_text())["entries"][0]["can_keep"] is False


def test_a_failed_write_of_the_view_only_warns(env, fake, monkeypatch, capsys):
    fake.schedule((aid(1), 29))

    def broken(*a, **k):
        raise state.StateError("cannot write leaving.json: disk full")

    monkeypatch.setattr(state, "save_leaving", broken)
    assert main(["run"], {**env, **LIVE}) == 0
    assert "[warn] cannot write leaving.json" in capsys.readouterr().err


def test_leaving_rows_clean_titles():
    e = core.Entry("a" * 32, "Bad\x1b[31m<b>", 1.0, 3, 0, True)
    rows = core.leaving_rows([e], {}, 0.0, 30)
    assert rows[0]["title"] == "Bad??31m?b?"


# ---------------------------------------------------------------- who kept it, and the history route
def test_keep_body_accepts_an_optional_by():
    assert api.parse_keep_body(b'{"title": "a"}') == ("a", None, "")
    assert api.parse_keep_body(b'{"title": " a ", "by": "Ann Lee"}') == ("a", "Ann Lee", "")
    for raw in (
        b'{"by": "ann"}',
        b'{"title": "a", "by": 5}',
        b'{"title": "a", "by": ""}',
        b'{"title": "a", "by": " ann"}',
        b'{"title": "a", "by": "bad\\nname"}',
        b'{"title": "a", "by": "' + b"a" * 61 + b'"}',
        b'{"title": "a", "by": "x", "by": "y"}',
        b'{"title": "a", "other": "x"}',
    ):
        assert api.parse_keep_body(raw)[0] is None, raw


def test_a_keep_over_the_api_records_who_and_shows_in_the_history_route(serve, fake, env):
    fake.schedule((aid(1), 29))
    c = serve(LIVE)
    assert c.call("POST", "/v1/keep", body={"title": "Alpha Movie", "by": "Ann"})[0] == 200
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]["by"] == "Ann"
    status, _, doc = c.call("GET", "/v1/keeps")
    assert status == 200 and doc["version"] == 1
    assert [(k["id"], k["title"], k["by"]) for k in doc["keeps"]] == [(aid(1), "Alpha Movie", "Ann")]
    entry = c.call("GET", "/v1/leaving")[2]["entries"][0]
    assert entry["can_keep"] is False and entry["kept"]["by"] == "Ann"


def test_the_history_route_is_empty_before_any_keep_and_needs_get_and_the_token(serve):
    c = serve()
    assert c.call("GET", "/v1/keeps")[2] == {"version": 1, "keeps": []}
    assert c.call("POST", "/v1/keeps", body={})[0] == 405
    assert c.call("GET", "/v1/keeps", token="wrong" * 8)[0] == 401


def test_the_person_limit_over_the_api_is_a_422_and_a_bad_by_is_a_400(serve, fake):
    fake.schedule((aid(1), 29))
    c = serve({**LIVE, "LOMBO_FAREWELL_MAX_KEEPS_PER_PERSON": "1"})
    status, _, body = c.call("POST", "/v1/keep", body={"title": "Alpha Movie"})
    assert status == 422 and "say who is keeping it" in body["message"]
    assert c.call("POST", "/v1/keep", body={"title": "Alpha Movie", "by": ""})[0] == 400
    assert fake.postponed == []


def test_the_history_route_works_without_a_keeps_function():
    import urllib.request

    cfg = from_env(
        {
            "LOMBO_FAREWELL_JELLYFIN_URL": "http://x",
            "LOMBO_FAREWELL_JELLYFIN_API_KEY": "k",
            "LOMBO_FAREWELL_MAINTAINERR_URL": "http://x",
            "LOMBO_FAREWELL_API_TOKEN": TOKEN,
        }
    )
    server = api.make_server(cfg, lambda t, b: (True, "ok"), dict, bind=("127.0.0.1", 0), log=lambda t: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/v1/keeps", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        with urllib.request.urlopen(req) as r:  # noqa: S310
            assert json.loads(r.read()) == {"version": 1, "keeps": []}
    finally:
        server.shutdown()
        server.server_close()
