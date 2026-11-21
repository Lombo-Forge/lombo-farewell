"""Regression tests for the findings of the third audit (AUDIT-farewell.md). Each one failed on the old code."""

import fcntl
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from conftest import aid
from lombo_farewell import cli, core, state
from lombo_farewell.cli import main
from lombo_farewell.config import ConfigError, from_env
from lombo_farewell.core import Candidate
from lombo_farewell.jellyfin import Jellyfin, JellyfinError
from lombo_farewell.sources import SourceError, maintainerr_candidates, parse_list, parse_when

LIVE = {"LOMBO_FAREWELL_DRY_RUN": "false"}


def file_env(env, tmp_path, entries, **extra):
    lst = tmp_path / "leaving.json"
    lst.write_text(json.dumps(entries))
    return {**env, **LIVE, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": str(lst), **extra}


# -- 1 and 2. a list that shrinks must not empty the collection ------------------------------------------------


def test_a_list_that_suddenly_goes_empty_removes_nothing(env, fake, capsys):
    live = {**env, **LIVE}
    fake.items = {aid(n): f"Title {n}" for n in range(1, 7)}
    fake.schedule(*[(aid(n), 20) for n in range(1, 7)])
    assert main(["run"], live) == 0
    fake.schedule()  # every collection inactive, or a renamed field: an empty answer that "read fine"
    for _ in range(3):
        assert main(["run"], live) == 0
    assert len(fake.members()) == 6
    assert "ALLOW_SHRINK" in capsys.readouterr().out


def test_a_list_that_drops_under_half_removes_nothing_until_allowed(env, fake):
    live = {**env, **LIVE}
    fake.items = {aid(n): f"Title {n}" for n in range(1, 7)}
    fake.schedule(*[(aid(n), 20) for n in range(1, 7)])
    assert main(["run"], live) == 0
    fake.schedule((aid(1), 20), (aid(2), 20))
    assert main(["run"], live) == 0
    assert len(fake.members()) == 6
    assert main(["run"], {**live, "LOMBO_FAREWELL_ALLOW_SHRINK": "true"}) == 0
    assert sorted(fake.members()) == [aid(1), aid(2)]


def test_a_normal_small_change_still_goes_through(env, fake):
    live = {**env, **LIVE}
    fake.items = {aid(n): f"Title {n}" for n in range(1, 7)}
    fake.schedule(*[(aid(n), 20) for n in range(1, 7)])
    assert main(["run"], live) == 0
    fake.schedule(*[(aid(n), 20) for n in range(1, 5)])
    assert main(["run"], live) == 0
    assert len(fake.members()) == 4


def test_diff_never_removes_titles_the_source_still_lists_but_jellyfin_did_not_match():
    have = {aid(1), aid(2), aid(3)}
    d = core.diff(have, [aid(1)], 25, keep_ids=frozenset({aid(2)}))
    assert d.remove == (aid(3),)


def test_ids_jellyfin_does_not_know_stay_in_the_collection(env, fake):
    live = {**env, **LIVE, "LOMBO_FAREWELL_ALLOW_SHRINK": "true"}
    fake.schedule((aid(1), 20), (aid(2), 20))
    assert main(["run"], live) == 0
    fake.items = {}  # the library is unmounted: Jellyfin knows none of the ids, Maintainerr still lists them
    assert main(["run"], live) == 0
    assert sorted(fake.members()) == [aid(1), aid(2)]


def test_a_title_that_becomes_ambiguous_stays_listed(env, fake, tmp_path):
    first = file_env(env, tmp_path, [{"id": aid(3), "leaves": "2027-01-01"}])
    assert main(["run"], first) == 0
    assert fake.members() == [aid(3)]
    # aid(3) and aid(4) are both called "Gamma Film": the name no longer points at one title
    second = file_env(
        env, tmp_path, [{"title": "Gamma Film", "leaves": "2027-01-01"}], LOMBO_FAREWELL_ALLOW_SHRINK="true"
    )
    assert main(["run"], second) == 0
    assert aid(3) in fake.members()


# -- 3. a collection the tool did not make -------------------------------------------------------------------


def test_a_collection_the_tool_did_not_make_is_left_alone(env, fake, capsys):
    fake.collections["c1"] = {"Name": "Leaving soon", "members": [aid(1), aid(2)]}
    fake.schedule((aid(1), 20))
    assert main(["run"], {**env, **LIVE}) == 0
    assert fake.members() == [aid(1), aid(2)]
    assert fake.writes() == []
    out = capsys.readouterr().out
    assert "was not made by this tool" in out
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["owned"] is None


def test_adopting_a_collection_on_purpose_works_and_is_remembered(env, fake):
    fake.collections["c1"] = {"Name": "Leaving soon", "members": [aid(1), aid(2)]}
    fake.schedule((aid(1), 20))
    assert main(["run"], {**env, **LIVE, "LOMBO_FAREWELL_ADOPT": "true"}) == 0
    assert fake.members() == [aid(1)]
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["owned"] == "c1"
    fake.schedule((aid(1), 20), (aid(2), 20))
    assert main(["run"], {**env, **LIVE}) == 0  # no ADOPT needed any more
    assert sorted(fake.members()) == [aid(1), aid(2)]


def test_a_collection_the_tool_made_is_recorded(env, fake):
    fake.schedule((aid(1), 20))
    assert main(["run"], {**env, **LIVE}) == 0
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["owned"] == "c1"


# -- 4. keep cannot be repeated at once -------------------------------------------------------------------------


def test_keep_is_refused_again_inside_keep_days_even_when_the_cleanup_tool_would_allow_it(env, fake, capsys):
    fake.maint_collections = [{"id": 7, "isActive": True, "deleteAfterDays": 90}]
    fake.schedule((aid(1), 85))  # 5 days left, 90 days of normal wait: always "inside the wait"
    live = {**env, **LIVE}
    assert main(["keep", "Alpha Movie"], live) == 0
    assert main(["keep", "Alpha Movie"], live) == 1
    assert "already kept" in capsys.readouterr().err
    assert len(fake.postponed) == 1
    # after KEEP_DAYS it may be kept again
    from lombo_farewell.config import from_env as load

    assert cli.keep(load(live), "Alpha Movie", now=time.time() + 31 * 86400) == 0
    assert len(fake.postponed) == 2


# -- 5. the API key never follows a redirect ---------------------------------------------------------------------


class Recorder(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []
    target = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/start"):
            self.send_response(302)
            self.send_header("Location", type(self).target + "/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        type(self).seen.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


def test_a_redirect_is_refused_and_the_key_is_not_sent_on(fake):
    class Other(Recorder):
        seen: ClassVar[list] = []

    other = HTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()

    class First(Recorder):
        seen: ClassVar[list] = []
        target = f"http://127.0.0.1:{other.server_port}"

    first = HTTPServer(("127.0.0.1", 0), First)
    threading.Thread(target=first.serve_forever, daemon=True).start()
    try:
        jf = Jellyfin(f"http://127.0.0.1:{first.server_port}/start", "secret-key")
        with pytest.raises(JellyfinError, match="redirect"):
            jf.info()
        assert Other.seen == []
    finally:
        first.shutdown()
        other.shutdown()


# -- 6. odd numbers and odd keys ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 1e30, -1e30])
def test_unusable_times_are_rejected(value):
    with pytest.raises(ValueError):
        parse_when(value)


def test_an_infinite_or_huge_wait_in_maintainerr_skips_the_collection():
    media = {7: [{"mediaServerId": aid(1), "addDate": "2026-12-01"}]}
    for grace in (float("inf"), float("nan"), 1e30, -5):
        assert maintainerr_candidates([{"id": 7, "isActive": True, "deleteAfterDays": grace}], media) == []


def test_a_list_file_with_an_absurd_date_is_an_error():
    with pytest.raises(SourceError):
        parse_list('[{"title": "x", "leaves": 1e999}]')


def test_plan_survives_a_non_finite_date_and_the_day_helper_too():
    cand = Candidate(aid(1), None, float("inf"), 30.0)
    assert core.plan([(cand, aid(1), "x")], 0.0, True)[0].days_left is None
    assert cli._day(float("inf")) == "unknown"
    assert cli._day(1e30) == "unknown"


def test_an_api_key_with_a_newline_is_refused_at_start_up():
    base = {
        "LOMBO_FAREWELL_JELLYFIN_URL": "http://jellyfin:8096",
        "LOMBO_FAREWELL_MAINTAINERR_URL": "http://maintainerr:6246",
        "LOMBO_FAREWELL_JELLYFIN_API_KEY": "abc\ndef",
    }
    with pytest.raises(ConfigError, match="JELLYFIN_API_KEY"):
        from_env(base)


def test_the_loop_survives_an_unexpected_error(env, monkeypatch, capsys):
    calls = []

    def boom(cfg):
        raise OverflowError("odd number")

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_once", boom)
    monkeypatch.setattr(cli.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop"], {**env, **LIVE})
    assert calls == [3600, 3600]
    assert "OverflowError" in capsys.readouterr().err


# -- 7. titles are cleaned before they are printed or sent ----------------------------------------------------


def test_clean_removes_escapes_newlines_and_chat_markup():
    out = core.clean("a\x1b[2J\nb @everyone <!channel>")
    assert not any(c in out for c in "\x1b\n@<>[]")


def test_a_hostile_title_does_not_reach_the_terminal(env, fake, capsys):
    fake.items[aid(1)] = "Bad\x1b[2J\nFAKE LINE @everyone"
    fake.schedule((aid(1), 20))
    assert main(["plan"], env) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\nFAKE LINE" not in out


def test_an_overdue_alert_is_cleaned(env, fake):
    fake.items[aid(1)] = "Late <!channel> title"
    fake.schedule((aid(1), 40))  # 10 days overdue
    assert main(["run"], {**env, **LIVE}) == 0
    hooks = [r for r in fake.requests if r["path"] == "/hook"]
    assert hooks
    assert b"<!channel>" not in hooks[0]["body"]


# -- 8. two runs at once ------------------------------------------------------------------------------------------


def test_the_lock_makes_a_second_run_wait(tmp_path):
    path = str(tmp_path / "state.json")
    done = threading.Event()
    with state.locked(path):

        def second():
            with state.locked(path):
                done.set()

        t = threading.Thread(target=second, daemon=True)
        t.start()
        assert not done.wait(0.3)  # still waiting while the first holds the lock
    assert done.wait(3)


def test_the_state_file_is_written_without_leftovers(tmp_path):
    path = str(tmp_path / "state.json")
    state.save(path, {"kept": {}, "alerted": ["x"], "owned": "c1"})
    assert state.load(path)["owned"] == "c1"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_a_stale_lock_file_does_not_block(tmp_path):
    path = str(tmp_path / "state.json")
    (tmp_path / "state.json.lock").write_text("")  # left over from a run that died: no flock is held on it
    with state.locked(path):
        pass
    handle = open(path + ".lock")  # noqa: SIM115
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again after the block
    handle.close()


# -- 11. misspelled settings -------------------------------------------------------------------------------------


def test_a_misspelled_setting_is_reported(env, fake, capsys):
    assert main(["plan"], {**env, "LOMBO_FAREWELL_MAX_REMOVALS_PER_ROUNDS": "1"}) == 0
    assert "MAX_REMOVALS_PER_ROUNDS" in capsys.readouterr().err
