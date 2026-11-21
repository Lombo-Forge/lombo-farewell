import json
import re
from pathlib import Path

import pytest

from conftest import NOW, aid
from lombo_farewell import cli, notify, state
from lombo_farewell.cli import main
from lombo_farewell.config import PREFIX, SETTINGS, ConfigError, from_env
from lombo_farewell.jellyfin import BATCH, Jellyfin, JellyfinError

ROOT = Path(__file__).resolve().parent.parent
LIVE = {"LOMBO_FAREWELL_DRY_RUN": "false"}


# ---------------------------------------------------------------- settings
def test_config_defaults_are_safe(env):
    cfg = from_env(env)
    assert cfg.dry_run is True
    assert cfg.interval_seconds == 3600
    assert cfg.source == "maintainerr"
    assert cfg.collection_name == "Leaving soon"
    assert cfg.keep_days == 30


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOMBO_FAREWELL_JELLYFIN_URL", "jellyfin:8096"),
        ("LOMBO_FAREWELL_JELLYFIN_API_KEY", "changeme"),
        ("LOMBO_FAREWELL_WEBHOOK_URL", "ftp://x"),
        ("LOMBO_FAREWELL_INTERVAL_SECONDS", "5"),
        ("LOMBO_FAREWELL_INTERVAL_SECONDS", "soon"),
        ("LOMBO_FAREWELL_SOURCE", "plex"),
        ("LOMBO_FAREWELL_MAINTAINERR_URL", "maintainerr"),
        ("LOMBO_FAREWELL_KEEP_DAYS", "0"),
        ("LOMBO_FAREWELL_KEEP_DAYS", "many"),
        ("LOMBO_FAREWELL_MAX_REMOVALS_PER_ROUND", "0"),
        ("LOMBO_FAREWELL_COLLECTION_NAME", "x" * 101),
        ("LOMBO_FAREWELL_DRY_RUN", "treu"),
    ],
)
def test_config_rejects_bad_values_and_names_the_setting(env, name, value):
    with pytest.raises(ConfigError, match=name):
        from_env({**env, name: value})


def test_file_source_needs_a_file_and_no_maintainerr_url(env):
    base = {k: v for k, v in env.items() if "MAINTAINERR" not in k}
    with pytest.raises(ConfigError, match="SOURCE_FILE"):
        from_env({**base, "LOMBO_FAREWELL_SOURCE": "file"})
    cfg = from_env({**base, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": "/data/list.txt"})
    assert cfg.source_file == "/data/list.txt"


def test_env_example_lists_every_setting_and_only_those():
    text = (ROOT / ".env.example").read_text()
    listed = set(re.findall(rf"^#?\s*{PREFIX}([A-Z_]+)=", text, re.M))
    assert listed == set(SETTINGS)
    source = (ROOT / "src/lombo_farewell/config.py").read_text()
    assert set(re.findall(r'get\("([A-Z_]+)"', source)) == set(SETTINGS)


def test_env_example_is_safe_to_copy():
    assert f"{PREFIX}DRY_RUN=true" in (ROOT / ".env.example").read_text()


def test_bad_settings_exit_2(capsys):
    assert main(["plan"], {}) == 2
    assert "LOMBO_FAREWELL_JELLYFIN_URL" in capsys.readouterr().err


# ---------------------------------------------------------------- Jellyfin client
def test_client_sends_the_key_and_reports_failures(fake):
    assert Jellyfin(fake.url, "good-key").names([aid(1), aid(99)]) == {aid(1): "Alpha Movie"}
    assert fake.requests[-1]["auth"] == 'MediaBrowser Token="good-key"'
    with pytest.raises(JellyfinError, match="HTTP 401"):
        Jellyfin(fake.url, "wrong").info()
    with pytest.raises(JellyfinError, match=r"/System/Info/Public"):
        Jellyfin("http://127.0.0.1:1", "k", timeout=1).public_info()


def test_client_rejects_odd_replies(fake):
    jf = Jellyfin(fake.url, "good-key")
    fake.query_items = lambda q: "oops"  # type: ignore[method-assign]
    fake.requests.clear()
    with pytest.raises(JellyfinError, match="unexpected reply"):
        jf.find_collection("x")


def test_a_reply_that_is_not_json_is_an_error(fake):
    fake.garbage = True
    with pytest.raises(JellyfinError, match="not JSON"):
        Jellyfin(fake.url, "good-key").public_info()


def test_client_batches_long_id_lists(fake):
    ids = [aid(1000 + n) for n in range(BATCH * 2 + 3)]
    jf = Jellyfin(fake.url, "good-key")
    coll = jf.create_collection("Leaving soon", ids)
    assert len(fake.collections[coll]["members"]) == len(ids)
    assert len(fake.sent("POST")) == 3  # one create (50 ids) + two adds
    jf.remove_from_collection(coll, ids)
    assert fake.collections[coll]["members"] == []
    assert [len(r["q"]["ids"].split(",")) for r in fake.sent("DELETE")] == [BATCH, BATCH, 3]


def test_ids_named_matches_exact_names_only(fake):
    jf = Jellyfin(fake.url, "good-key")
    assert jf.ids_named("alpha movie") == [aid(1)]
    assert jf.ids_named("Alpha") == []
    assert jf.ids_named("Gamma Film") == [aid(3), aid(4)]


def test_create_collection_finds_it_when_the_reply_has_no_id(fake):
    jf = Jellyfin(fake.url, "good-key")
    orig = jf._call

    def no_id(method, path, params=None, auth=True):
        res = orig(method, path, params, auth)
        return None if (method, path) == ("POST", "/Collections") else res

    jf._call = no_id  # type: ignore[method-assign]
    assert jf.create_collection("Another", [aid(2)]) == "c1"


def test_create_collection_fails_loudly_when_it_cannot_be_found(fake):
    jf = Jellyfin(fake.url, "good-key")
    jf._call = lambda method, path, params=None, auth=True: {"Items": []} if method == "GET" else None  # type: ignore[method-assign]
    with pytest.raises(JellyfinError, match="cannot be found"):
        jf.create_collection("Gone", [aid(1)])


def test_notify_posts_json(fake):
    notify.send(fake.url + "/hook", "hello")
    assert json.loads(fake.sent("POST")[0]["body"]) == {"content": "hello", "text": "hello"}
    with pytest.raises(notify.NotifyError):
        notify.send("http://127.0.0.1:1/hook", "x", timeout=1)


# ---------------------------------------------------------------- doctor
def test_doctor_passes_and_fails(env, fake, capsys):
    fake.schedule((aid(1), 20))
    assert main(["doctor"], env) == 0
    out = capsys.readouterr().out
    assert "1 title(s) scheduled" in out
    assert "does not exist yet" in out
    assert "dry run" in out
    assert main(["doctor"], {**env, "LOMBO_FAREWELL_JELLYFIN_API_KEY": "wrong"}) == 1
    assert "API key accepted: /System/Info: HTTP 401" in capsys.readouterr().out


def test_doctor_reports_a_broken_source_and_an_existing_collection(env, fake, capsys, tmp_path):
    fake.maint_status = 500
    fake.collections["c1"] = {"Name": "Leaving soon", "members": []}
    assert main(["doctor"], env) == 1
    out = capsys.readouterr().out
    assert "[FAIL] source readable" in out
    assert "'Leaving soon' exists" in out
    lst = tmp_path / "l.txt"
    lst.write_text("Alpha Movie\n")
    file_env = {**env, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": str(lst)}
    assert main(["doctor"], file_env) == 0
    assert "cannot postpone" in capsys.readouterr().out


# ---------------------------------------------------------------- plan and run
def test_plan_changes_nothing_and_run_is_quiet_while_dry(env, fake, capsys):
    fake.schedule((aid(1), 29), (aid(2), 5))
    assert main(["plan"], env) == 0
    out = capsys.readouterr().out
    assert "leaving: Alpha Movie (1 day(s) left)" in out
    assert "would create it with 2 title(s)" in out
    assert fake.writes() == []
    assert main(["run"], env) == 0
    assert "Dry run: nothing was changed" in capsys.readouterr().out
    assert fake.writes() == []
    assert not Path(env["LOMBO_FAREWELL_STATE_FILE"]).exists()


def test_live_run_creates_then_updates_the_collection(env, fake, capsys):
    live = {**env, **LIVE}
    fake.schedule((aid(1), 29), (aid(2), 5))
    assert main(["run"], live) == 0
    assert fake.members() == [aid(1), aid(2)]  # soonest first
    assert fake.collections["c1"]["Name"] == "Leaving soon"
    capsys.readouterr()
    # Alpha leaves the list (watched), Gamma arrives
    fake.schedule((aid(2), 5), (aid(3), 20))
    assert main(["run"], live) == 0
    assert sorted(fake.members()) == sorted([aid(2), aid(3)])
    out = capsys.readouterr().out
    assert "collection: add Gamma Film" in out
    assert "collection: remove Alpha Movie" in out
    # a third run with nothing new writes nothing
    before = len(fake.writes())
    assert main(["run"], live) == 0
    assert len(fake.writes()) == before


def test_a_title_in_two_maintainerr_groups_is_listed_once(env, fake):
    fake.maint_collections.append({"id": 8, "isActive": True, "deleteAfterDays": 60})
    fake.maint_media[8] = []
    fake.schedule((aid(1), 29))
    fake.maint_media[8] = [dict(fake.maint_media[7][0])]
    assert main(["run"], {**env, **LIVE}) == 0
    assert fake.members() == [aid(1)]


def test_nothing_scheduled_makes_no_empty_collection(env, fake, capsys):
    assert main(["run"], {**env, **LIVE}) == 0
    assert fake.collections == {}
    assert "Nothing is scheduled" in capsys.readouterr().out


def test_a_broken_source_leaves_the_collection_untouched(env, fake, capsys):
    live = {**env, **LIVE}
    fake.schedule((aid(1), 29), (aid(2), 5))
    assert main(["run"], live) == 0
    fake.requests.clear()
    for broken in (500, 404):
        fake.maint_status = broken
        assert main(["run"], live) == 1
        assert fake.members() == [aid(1), aid(2)]
        assert fake.writes() == []
    assert "[FAIL] Maintainerr" in capsys.readouterr().err


def test_an_unreachable_jellyfin_changes_nothing(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["run"], {**env, **LIVE, "LOMBO_FAREWELL_JELLYFIN_URL": "http://127.0.0.1:1"}) == 1
    assert "[FAIL]" in capsys.readouterr().err
    assert fake.writes() == []


def test_a_source_that_suddenly_lists_nothing_removes_only_a_few_per_round(env, fake):
    # The shrink guard (tests/test_fixes3.py) would stop this outright; here it is allowed, to test the cap itself.
    live = {**env, **LIVE, "LOMBO_FAREWELL_MAX_REMOVALS_PER_ROUND": "2", "LOMBO_FAREWELL_ALLOW_SHRINK": "true"}
    fake.items = {aid(n): f"Title {n}" for n in range(1, 7)}
    fake.schedule(*[(aid(n), 20) for n in range(1, 7)])
    assert main(["run"], live) == 0
    assert len(fake.members()) == 6
    fake.schedule()  # Maintainerr answers with an empty list
    assert main(["run"], live) == 0
    assert len(fake.members()) == 4  # only two went
    fake.schedule(*[(aid(n), 20) for n in range(1, 7)])  # it recovers: they come back
    assert main(["run"], live) == 0
    assert len(fake.members()) == 6


def test_plan_names_what_is_held_back(env, fake, capsys):
    live = {**env, **LIVE, "LOMBO_FAREWELL_MAX_REMOVALS_PER_ROUND": "1", "LOMBO_FAREWELL_ALLOW_SHRINK": "true"}
    fake.schedule((aid(1), 20), (aid(2), 20))
    assert main(["run"], live) == 0
    fake.schedule()
    capsys.readouterr()
    assert main(["plan"], live) == 0
    out = capsys.readouterr().out
    assert "collection: remove " in out
    assert "collection: leave in place for now " in out
    assert "Nothing is scheduled" not in out


def test_the_app_never_deletes_a_title_only_collection_membership(env, fake):
    live = {**env, **LIVE}
    fake.schedule((aid(1), 20), (aid(2), 20))
    main(["run"], live)
    fake.schedule((aid(2), 20))
    main(["run"], live)
    for r in fake.sent("DELETE"):
        assert r["path"].startswith("/Collections/")
    assert set(fake.items) == {aid(1), aid(2), aid(3), aid(4)}


def test_titles_jellyfin_does_not_know_are_skipped_with_a_note(env, fake, capsys):
    fake.schedule((aid(1), 20), (aid(50), 20))
    assert main(["plan"], env) == 0
    out = capsys.readouterr().out
    assert "skipped: " in out
    assert "Jellyfin does not know this id" in out
    assert "would create it with 1 title(s)" in out


def test_a_write_failure_part_way_is_reported_and_never_shortens_the_collection(env, fake, capsys):
    live = {**env, **LIVE}
    fake.schedule((aid(1), 20))
    assert main(["run"], live) == 0
    fake.schedule((aid(1), 20), (aid(2), 20))
    fake.fail_collections_write = True
    assert main(["run"], live) == 1
    assert fake.members() == [aid(1)]
    assert "HTTP 500" in capsys.readouterr().err


# ---------------------------------------------------------------- file source
def test_file_source_mirrors_titles_resolved_by_name(env, fake, capsys, tmp_path):
    lst = tmp_path / "leaving.txt"
    lst.write_text("Alpha Movie | 2026-12-01\nBeta Show\nNo Such Title\nGamma Film\n")
    file_env = {**env, **LIVE, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": str(lst)}
    assert main(["run"], file_env) == 0
    assert sorted(fake.members()) == sorted([aid(1), aid(2)])  # unknown and ambiguous names are skipped
    out = capsys.readouterr().out
    assert "No Such Title: no title with exactly this name" in out
    assert "Gamma Film: 2 titles with exactly this name" in out
    # the file source cannot postpone
    assert main(["keep", "Alpha Movie"], file_env) == 1
    assert "cannot postpone" in capsys.readouterr().err


def test_file_source_by_id_and_a_missing_file_changes_nothing(env, fake, tmp_path):
    lst = tmp_path / "leaving.json"
    lst.write_text(json.dumps([{"id": aid(1).upper(), "leaves": "2026-12-01"}]))
    file_env = {**env, **LIVE, "LOMBO_FAREWELL_SOURCE": "file", "LOMBO_FAREWELL_SOURCE_FILE": str(lst)}
    assert main(["run"], file_env) == 0
    assert fake.members() == [aid(1)]
    lst.unlink()
    fake.requests.clear()
    assert main(["run"], file_env) == 1
    assert fake.members() == [aid(1)]
    assert fake.writes() == []


# ---------------------------------------------------------------- keep
def test_keep_dry_run_postpones_nothing(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "alpha"], env) == 0
    assert "Dry run: would keep Alpha Movie for 30 more days" in capsys.readouterr().out
    assert fake.postponed == []


def test_keep_postpones_through_the_source_and_remembers_it(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 0
    assert fake.postponed == [{"collectionId": 7, "mediaId": aid(1), "days": 30}]
    assert "it now leaves on 2027-01-15" in capsys.readouterr().out
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]["title"] == "Alpha Movie"


def test_keep_twice_in_a_row_is_refused(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 0
    fake.schedule((aid(1), -1))  # Maintainerr now says it leaves in 31 days: beyond the normal wait
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 1
    assert "already kept" in capsys.readouterr().err
    assert len(fake.postponed) == 1


@pytest.mark.parametrize("query", ["zzz", "", "a"])
def test_keep_refuses_unknown_empty_or_ambiguous_titles(env, fake, capsys, query):
    fake.schedule((aid(1), 29), (aid(2), 29))
    assert main(["keep", query], {**env, **LIVE}) == 1
    assert "[FAIL]" in capsys.readouterr().err
    assert fake.postponed == []


def test_keep_with_the_source_down_does_nothing(env, fake, capsys):
    fake.maint_status = 500
    assert main(["keep", "Alpha"], {**env, **LIVE}) == 1
    assert "[FAIL]" in capsys.readouterr().err
    assert fake.postponed == []


def test_keep_when_the_postpone_call_fails_records_nothing(env, fake, capsys):
    fake.schedule((aid(1), 29))
    fake.postpone_reply = None
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 1
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"] == {}


# ---------------------------------------------------------------- overdue alerts and state
def test_overdue_alert_is_sent_once_and_only_when_live(env, fake):
    fake.schedule((aid(1), 35))  # 5 days past a 30-day wait
    assert main(["run"], env) == 0  # dry run
    assert [r for r in fake.sent("POST") if r["path"] == "/hook"] == []
    live = {**env, **LIVE}
    assert main(["run"], live) == 0
    assert main(["run"], live) == 0
    hooks = [r for r in fake.sent("POST") if r["path"] == "/hook"]
    assert len(hooks) == 1
    assert "Alpha Movie" in json.loads(hooks[0]["body"])["content"]


def test_a_failing_webhook_does_not_stop_the_round_and_is_retried(env, fake, capsys):
    fake.schedule((aid(1), 35))
    live = {**env, **LIVE, "LOMBO_FAREWELL_WEBHOOK_URL": "http://127.0.0.1:1/hook"}
    assert main(["run"], live) == 0
    assert "[FAIL] webhook failed" in capsys.readouterr().err
    assert fake.members() == [aid(1)]
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["alerted"] == []


def test_no_webhook_means_no_alert_but_a_note(env, fake, capsys):
    fake.schedule((aid(1), 35))
    no_hook = {k: v for k, v in env.items() if "WEBHOOK" not in k}
    assert main(["run"], {**no_hook, **LIVE}) == 0
    assert "overdue: Alpha Movie" in capsys.readouterr().out


def test_state_forgets_kept_titles_that_left_the_list(env, fake):
    path = env["LOMBO_FAREWELL_STATE_FILE"]
    fake.schedule((aid(1), 29))
    main(["keep", "Alpha Movie"], {**env, **LIVE})
    fake.schedule((aid(2), 10))
    main(["run"], {**env, **LIVE})
    assert state.load(path)["kept"] == {}


def test_state_file_damage_and_write_errors(tmp_path, env, fake, capsys):
    p = tmp_path / "s.json"
    for text in ("{broken", "[]", '{"kept": 1, "alerted": [1, "a"]}'):
        p.write_text(text)
        assert state.load(str(p))["kept"] == {}
    assert state.load(str(p))["alerted"] == ["a"]
    assert state.load(str(tmp_path / "missing.json")) == state.empty()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = {**env, **LIVE, "LOMBO_FAREWELL_STATE_FILE": str(blocker / "state.json")}
    fake.schedule((aid(1), 20))
    assert main(["run"], bad) == 1
    assert "cannot write" in capsys.readouterr().err
    assert fake.members() == [aid(1)]  # the collection was already right; only the memory failed


# ---------------------------------------------------------------- loop
def test_loop_survives_a_bad_round(env, fake, monkeypatch, capsys):
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        if len(calls) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    fake.maint_status = 500
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--loop"], {**env, **LIVE})
    assert calls == [3600, 3600]
    assert capsys.readouterr().err.count("[FAIL]") == 2


def test_day_helper():
    assert cli._day(None) == "unknown"
    assert cli._day(NOW) == "2027-01-15"
