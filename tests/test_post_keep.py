"""The optional post-keep command: runs once after a keep that really postponed a title, never fails the keep."""

import json
import shlex
import subprocess
import sys
import threading
import time

import pytest

from conftest import aid
from lombo_farewell import api, cli
from lombo_farewell.cli import main
from lombo_farewell.config import ConfigError, from_env

LIVE = {"LOMBO_FAREWELL_DRY_RUN": "false"}
TOKEN = "t" * 32
UNTIL = 1_799_971_200  # 2027-01-15, the fake Maintainerr answer


def hook(tmp_path, exit_code=0):
    """A command that appends the JSON it was given to a file, then exits with exit_code."""
    out = tmp_path / "hook-out.txt"
    script = tmp_path / "hook.py"
    script.write_text(
        "import os, sys\n"
        f"open({str(out)!r}, 'a').write(os.environ['LOMBO_FAREWELL_KEPT_JSON'] + '\\n')\n"
        f"sys.exit({exit_code})\n"
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}", out


def with_hook(env, command, **extra):
    return {**env, **LIVE, "LOMBO_FAREWELL_POST_KEEP_COMMAND": command, **extra}


def test_runs_once_with_the_id_and_title_after_a_cli_keep(env, fake, tmp_path):
    fake.schedule((aid(1), 29))
    command, out = hook(tmp_path)
    assert main(["keep", "Alpha Movie"], with_hook(env, command)) == 0
    assert [json.loads(line) for line in out.read_text().splitlines()] == [
        {"id": aid(1), "title": "Alpha Movie", "by": None, "until": UNTIL}
    ]


def test_does_not_run_in_a_dry_run(env, fake, tmp_path):
    fake.schedule((aid(1), 29))
    command, out = hook(tmp_path)
    assert main(["keep", "Alpha Movie"], {**env, "LOMBO_FAREWELL_POST_KEEP_COMMAND": command}) == 0
    assert not out.exists()


def test_does_not_run_when_the_keep_is_refused(env, fake, tmp_path):
    fake.schedule((aid(1), 29))
    command, out = hook(tmp_path)
    assert main(["keep", "No Such Title"], with_hook(env, command)) == 1
    assert main(["keep", "Alpha Movie"], with_hook(env, command)) == 0
    fake.schedule((aid(1), -1))
    assert main(["keep", "Alpha Movie"], with_hook(env, command)) == 1  # already kept
    assert len(out.read_text().splitlines()) == 1


def test_a_failing_command_is_a_warning_not_a_failed_keep(env, fake, tmp_path, capsys):
    fake.schedule((aid(1), 29))
    command, _ = hook(tmp_path, exit_code=4)
    assert main(["keep", "Alpha Movie"], with_hook(env, command)) == 0
    cap = capsys.readouterr()
    assert "Kept Alpha Movie" in cap.out
    assert "post-keep command failed (exit 4)" in cap.err
    assert "LOMBO_FAREWELL_KEPT_JSON" not in cap.out + cap.err


def test_a_command_that_cannot_start_is_a_warning(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], with_hook(env, "/nonexistent/lombo-hook")) == 0
    assert "post-keep command failed (FileNotFoundError)" in capsys.readouterr().err


def test_timeout_is_a_warning_and_uses_sixty_seconds(env, fake, monkeypatch, capsys):
    fake.schedule((aid(1), 29))
    seen = {}

    def slow(args, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", slow)
    assert main(["keep", "Alpha Movie"], with_hook(env, "some-hook")) == 0
    assert "post-keep command failed (TimeoutExpired)" in capsys.readouterr().err
    assert seen["timeout"] == 60


def test_never_uses_a_shell_and_drops_output(env, fake, monkeypatch):
    fake.schedule((aid(1), 29))
    seen = {}

    def fake_run(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert main(["keep", "Alpha Movie"], with_hook(env, "hook 'two words' ; echo hi")) == 0
    assert seen["args"] == ["hook", "two words", ";", "echo", "hi"]
    assert not seen["kw"].get("shell")
    assert seen["kw"]["stdin"] == subprocess.DEVNULL
    assert seen["kw"]["capture_output"] is True
    assert json.loads(seen["kw"]["env"]["LOMBO_FAREWELL_KEPT_JSON"]) == {
        "id": aid(1),
        "title": "Alpha Movie",
        "by": None,
        "until": UNTIL,
    }


def test_empty_means_off_and_bad_syntax_stops_start_up(env, capsys):
    assert from_env(env).post_keep_command == ()
    assert from_env({**env, "LOMBO_FAREWELL_POST_KEEP_COMMAND": "  "}).post_keep_command == ()
    with pytest.raises(ConfigError, match="LOMBO_FAREWELL_POST_KEEP_COMMAND is not a valid command"):
        from_env({**env, "LOMBO_FAREWELL_POST_KEEP_COMMAND": "hook 'oops"})
    assert main(["plan"], {**env, "LOMBO_FAREWELL_POST_KEEP_COMMAND": "hook 'oops"}) == 2
    assert "LOMBO_FAREWELL_POST_KEEP_COMMAND" in capsys.readouterr().err


def test_the_command_runs_outside_the_run_lock(env, fake, tmp_path, monkeypatch):
    """A slow command must not hold the run lock: another keep or run can start while it works."""
    fake.schedule((aid(1), 29))
    state_file = env["LOMBO_FAREWELL_STATE_FILE"]
    free = []

    def check_lock(args, **kw):
        with cli.state.locked(state_file):  # would block forever if keep() still held it
            free.append(True)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", check_lock)
    assert main(["keep", "Alpha Movie"], with_hook(env, "some-hook")) == 0
    assert free == [True]


# ------------------------------------------------------------ through the HTTP API
@pytest.fixture
def served(monkeypatch):
    """Run the real `cli.serve` on a free port; yields start(env) -> port."""
    servers = []
    real = api.make_server

    def make(cfg, *args, **kw):
        server = real(cfg, *args, bind=("127.0.0.1", 0), log=lambda t: None, **kw)
        servers.append(server)
        return server

    monkeypatch.setattr(api, "make_server", make)

    def start(env):
        cfg = from_env({**env, "LOMBO_FAREWELL_API_TOKEN": TOKEN})
        threading.Thread(target=cli.serve, args=(cfg,), daemon=True).start()
        for _ in range(100):
            if servers:
                return servers[0].server_address[1]
            time.sleep(0.05)
        raise AssertionError("the server did not start")

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def post_keep(port, title):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/v1/keep",
        body=json.dumps({"title": title}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    return resp.status, body


def test_api_keep_runs_the_command_once(env, fake, tmp_path, served):
    fake.schedule((aid(1), 29))
    command, out = hook(tmp_path)
    port = served(with_hook(env, command))
    status, body = post_keep(port, "Alpha Movie")
    assert (status, body["ok"]) == (200, True)
    assert [json.loads(line) for line in out.read_text().splitlines()] == [
        {"id": aid(1), "title": "Alpha Movie", "by": None, "until": UNTIL}
    ]
    status, body = post_keep(port, "No Such Title")  # refused: the command does not run again
    assert status == 422
    assert len(out.read_text().splitlines()) == 1


def test_api_keep_with_a_failing_command_still_answers_ok_with_a_note(env, fake, tmp_path, served):
    fake.schedule((aid(1), 29))
    command, _ = hook(tmp_path, exit_code=2)
    status, body = post_keep(served(with_hook(env, command)), "Alpha Movie")
    assert (status, body["ok"]) == (200, True)
    assert body["message"].startswith("Kept Alpha Movie")
    assert body["message"].endswith("Note: post-keep command failed (exit 2).")


def test_api_dry_run_keep_does_not_run_the_command(env, fake, tmp_path, served):
    fake.schedule((aid(1), 29))
    command, out = hook(tmp_path)
    port = served({**env, "LOMBO_FAREWELL_POST_KEEP_COMMAND": command})
    assert post_keep(port, "Alpha Movie")[0] == 200
    assert not out.exists()


def test_api_keep_lock_is_free_while_the_command_runs(env, fake, monkeypatch, served):
    fake.schedule((aid(1), 29))
    freed = []

    def check(args, **kw):
        with cli.state.locked(env["LOMBO_FAREWELL_STATE_FILE"]):
            freed.append(True)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", check)
    assert post_keep(served(with_hook(env, "some-hook")), "Alpha Movie")[0] == 200
    assert freed == [True]
