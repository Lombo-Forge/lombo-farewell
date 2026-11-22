"""What a panel, a digest or another tool needs: richer leaving.json, a keep history, keep limits, alerts."""

import json
import os
import stat
import time
from pathlib import Path

import pytest

from conftest import DAY, NOW, aid
from lombo_farewell import cli, core, state
from lombo_farewell.cli import main
from lombo_farewell.config import ConfigError, from_env
from lombo_farewell.core import Candidate, Entry

LIVE = {"LOMBO_FAREWELL_DRY_RUN": "false"}


def folder(env):
    return Path(env["LOMBO_FAREWELL_STATE_FILE"]).parent


def leaving(env):
    return json.loads((folder(env) / "leaving.json").read_text())


def hooks(fake):
    return [json.loads(r["body"])["content"] for r in fake.sent("POST") if r["path"] == "/hook"]


# ---------------------------------------------------------------- core: the richer rows
def test_rows_carry_the_date_the_group_and_the_keep_note():
    e = Entry(aid(1), "Alpha", NOW + 2 * DAY, 2, 0, True, group="7", group_name="Movies")
    late = Entry(aid(2), "Beta", NOW - 3 * DAY, 0, 3, False)
    none = Entry(aid(3), "Gamma", None, None, 0, False)
    kept = {aid(1): {"by": "ann", "at": NOW - DAY, "until": NOW + 2 * DAY}}
    rows = core.leaving_rows([e, late, none], kept, NOW, 30)
    assert rows[0]["delete_at"] == int(NOW + 2 * DAY)
    assert rows[0]["overdue_days"] == 0
    assert (rows[0]["group"], rows[0]["group_name"]) == ("7", "Movies")
    assert rows[0]["kept"] == {"by": "ann", "at": int(NOW - DAY), "until": int(NOW + 2 * DAY)}
    assert rows[0]["can_keep"] is False  # kept a day ago
    assert rows[1]["overdue_days"] == 3
    assert rows[1]["kept"] is None
    assert (rows[2]["delete_at"], rows[2]["group"], rows[2]["group_name"]) == (None, None, None)


def test_a_damaged_keep_record_gives_nulls_not_a_crash():
    e = Entry(aid(1), "Alpha", NOW + DAY, 1, 0, True)
    rows = core.leaving_rows([e], {aid(1): {"by": 5, "at": "x", "until": float("nan")}}, NOW, 30)
    assert rows[0]["kept"] == {"by": None, "at": None, "until": None}
    assert core.kept_note(None) is None


def test_after_keep_moves_the_date_and_blocks_another_keep():
    e = Entry(aid(1), "Alpha", NOW + DAY, 1, 0, True)
    moved = core.after_keep(e, NOW + 31 * DAY, NOW)
    assert (moved.delete_at, moved.days_left, moved.overdue_days, moved.can_keep) == (NOW + 31 * DAY, 31, 0, False)


def test_a_postponed_title_shows_the_collection_name_from_maintainerr():
    from lombo_farewell import sources

    colls = [
        {"id": 7, "isActive": True, "deleteAfterDays": 30, "title": "Old movies"},
        {"id": 8, "isActive": True, "deleteAfterDays": 30, "title": 5},
    ]
    media = {
        7: [{"mediaServerId": aid(1), "addDate": "2027-01-01"}],
        8: [{"mediaServerId": aid(2), "addDate": "2027-01-01"}],
    }
    out = sources.maintainerr_candidates(colls, media)
    assert [(c.group, c.group_name) for c in out] == [("7", "Old movies"), ("8", None)]


# ---------------------------------------------------------------- core: limits and warnings
def test_limits_are_off_by_default():
    assert core.limit_problem(None, {"count": 99}, [], NOW, 30, 0, 0) == ""


def test_a_title_can_be_kept_only_so_many_times():
    assert core.limit_problem("ann", None, [], NOW, 30, 0, 1) == ""
    assert core.limit_problem("ann", {"count": 1}, [], NOW, 30, 0, 2) == ""
    assert "already kept 2 time(s)" in core.limit_problem("ann", {"count": 2}, [], NOW, 30, 0, 2)
    assert "already kept 1 time(s)" in core.limit_problem("ann", {"at": 1.0}, [], NOW, 30, 0, 1)  # old record: one keep


def test_a_person_can_keep_only_so_many_titles_per_period_ignoring_case():
    history = [
        {"by": "Ann", "at": NOW - 5 * DAY},
        {"by": "ann", "at": NOW - 40 * DAY},  # outside the 30 days
        {"by": "bob", "at": NOW - DAY},
        {"by": 3, "at": NOW},  # damaged rows are ignored
        {"by": "ann", "at": "x"},
    ]
    assert core.limit_problem("ANN", None, history, NOW, 30, 2, 0) == ""
    assert "has used 1 of 1 keeps allowed per 30 days" in core.limit_problem("ann", None, history, NOW, 30, 1, 0)
    assert core.limit_problem("cy", None, history, NOW, 30, 1, 0) == ""


def test_a_person_limit_needs_to_know_the_person():
    assert "say who is keeping it" in core.limit_problem(None, None, [], NOW, 30, 2, 0)


def test_valid_by():
    assert core.valid_by("Ann Lee")
    assert core.valid_by("a" * 60)
    for bad in ("", " ann", "ann ", "a" * 61, "a\nb", "a\x00"):
        assert not core.valid_by(bad)


def test_due_to_warn_picks_titles_within_a_day_once_and_forgets_the_rest():
    near = Entry(aid(1), "Near", NOW + 0.5 * DAY, 1, 0, True)
    far = Entry(aid(2), "Far", NOW + 9 * DAY, 9, 0, True)
    late = Entry(aid(3), "Late", NOW - 3 * DAY, 0, 3, True)
    nodate = Entry(aid(4), "None", None, None, 0, False)
    todo, remembered = core.due_to_warn([near, far, late, nodate], set())
    assert [e.title for e in todo] == ["Near"]
    assert remembered == {aid(1)}
    todo, remembered = core.due_to_warn([near, far], {aid(1)})
    assert todo == []
    assert remembered == {aid(1)}
    todo, remembered = core.due_to_warn([far], {aid(1)})  # kept or gone: forgotten
    assert todo == []
    assert remembered == set()


# ---------------------------------------------------------------- settings
def test_new_settings_default_to_off(env):
    cfg = from_env(env)
    assert (cfg.max_keeps_per_person, cfg.max_keeps_per_title) == (0, 0)
    assert (cfg.alert_on_keep, cfg.alert_deleting_soon) == (False, False)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_KEEPS_PER_PERSON", "-1"),
        ("MAX_KEEPS_PER_PERSON", "1001"),
        ("MAX_KEEPS_PER_PERSON", "two"),
        ("MAX_KEEPS_PER_TITLE", "-1"),
        ("MAX_KEEPS_PER_TITLE", "x"),
        ("ALERT_ON_KEEP", "treu"),
        ("ALERT_DELETING_SOON", "maybe"),
    ],
)
def test_bad_new_settings_name_the_variable(env, name, value):
    with pytest.raises(ConfigError, match=f"LOMBO_FAREWELL_{name}"):
        from_env({**env, f"LOMBO_FAREWELL_{name}": value})


def test_doctor_shows_the_limits_and_warns_about_alerts_without_a_webhook(env, fake, capsys):
    no_hook = {k: v for k, v in env.items() if "WEBHOOK" not in k}
    extra = {"LOMBO_FAREWELL_MAX_KEEPS_PER_PERSON": "2", "LOMBO_FAREWELL_ALERT_ON_KEEP": "true"}
    assert main(["doctor"], {**no_hook, **extra}) == 0
    out = capsys.readouterr()
    assert "keep limits: 2 per person per 30 days; none" in out.out
    assert "alerts (on keep) are on but LOMBO_FAREWELL_WEBHOOK_URL is not set" in out.out


# ---------------------------------------------------------------- keep: history, who, leaving.json
def test_a_keep_records_who_and_when_and_refreshes_leaving_json_at_once(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie", "--by", "Ann"], {**env, **LIVE}) == 0
    saved = state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]
    assert saved["by"] == "Ann"
    assert saved["count"] == 1
    assert saved["until"] == 1_799_971_200
    row = leaving(env)["entries"][0]  # no round has run: the keep itself wrote the file
    assert row["can_keep"] is False
    assert row["delete_at"] == 1_799_971_200
    assert row["overdue_days"] == 0
    assert row["kept"] == {"by": "Ann", "at": row["kept"]["at"], "until": 1_799_971_200}
    doc = json.loads((folder(env) / "keeps.json").read_text())
    assert doc["version"] == 1
    assert len(doc["keeps"]) == 1
    assert {k: doc["keeps"][0][k] for k in ("id", "title", "by", "until")} == {
        "id": aid(1),
        "title": "Alpha Movie",
        "by": "Ann",
        "until": 1_799_971_200,
    }
    assert abs(doc["keeps"][0]["at"] - time.time()) < 60
    assert stat.S_IMODE(os.stat(folder(env) / "keeps.json").st_mode) == 0o644


def test_a_keep_without_a_name_is_recorded_without_one(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 0
    assert leaving(env)["entries"][0]["kept"]["by"] is None
    assert json.loads((folder(env) / "keeps.json").read_text())["keeps"][0]["by"] is None


def test_a_dry_run_keep_writes_no_files(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie", "--by", "Ann"], env) == 0
    assert [f.name for f in folder(env).iterdir() if f.suffix != ".lock"] == []  # the run lock is not data


def test_a_refused_keep_adds_nothing_to_the_history(env, fake):
    fake.schedule((aid(1), 29))
    live = {**env, **LIVE}
    assert main(["keep", "Alpha Movie"], live) == 0
    assert main(["keep", "Alpha Movie"], live) == 1
    assert len(json.loads((folder(env) / "keeps.json").read_text())["keeps"]) == 1


def test_a_bad_by_is_refused_before_anything_happens(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie", "--by", " x"], {**env, **LIVE}) == 2
    assert "--by must be 1 to 60 printable characters" in capsys.readouterr().err
    assert fake.postponed == []


def test_a_failed_write_of_the_history_only_warns(env, fake, monkeypatch, capsys):
    fake.schedule((aid(1), 29))

    def broken(*a, **k):
        raise state.StateError("cannot write keeps.json: disk full")

    monkeypatch.setattr(state, "append_keep", broken)
    assert main(["keep", "Alpha Movie"], {**env, **LIVE}) == 0
    cap = capsys.readouterr()
    assert "Kept Alpha Movie" in cap.out
    assert "[warn] cannot write keeps.json" in cap.err
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]["title"] == "Alpha Movie"


def test_the_history_keeps_only_the_newest_rows_and_survives_damage(tmp_path):
    path = str(tmp_path / "state.json")
    assert state.load_keeps(path) == []
    for n in range(state.KEEPS_MAX + 3):
        state.append_keep(path, {"at": n, "id": "x", "title": "t", "by": None, "until": n})
    rows = state.load_keeps(path)
    assert len(rows) == state.KEEPS_MAX
    assert rows[0]["at"] == 3
    assert rows[-1]["at"] == state.KEEPS_MAX + 2
    for text in ("{broken", "[]", '{"keeps": 1}', '{"keeps": [1, {"at": 1}]}'):
        state.keeps_path(path).write_text(text)
        assert state.load_keeps(path) in ([], [{"at": 1}])


# ---------------------------------------------------------------- keep limits through the CLI
def test_a_person_limit_stops_a_third_keep_and_needs_a_name(env, fake, capsys):
    fake.schedule((aid(1), 29), (aid(2), 29))
    fake.items[aid(2)] = "Beta Show"
    live = {**env, **LIVE, "LOMBO_FAREWELL_MAX_KEEPS_PER_PERSON": "1"}
    assert main(["keep", "Alpha Movie"], live) == 1
    assert "say who is keeping it" in capsys.readouterr().err
    assert main(["keep", "Alpha Movie", "--by", "Ann"], live) == 0
    assert main(["keep", "Beta Show", "--by", "ann"], live) == 1
    assert "has used 1 of 1 keeps allowed per 30 days" in capsys.readouterr().err
    assert main(["keep", "Beta Show", "--by", "Bob"], live) == 0
    assert len(fake.postponed) == 2


def test_the_person_limit_applies_in_a_dry_run_too(env, fake, capsys):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, "LOMBO_FAREWELL_MAX_KEEPS_PER_PERSON": "1"}) == 1
    assert "say who is keeping it" in capsys.readouterr().err


def test_the_person_window_is_keep_days(env, fake):
    fake.schedule((aid(1), 29))
    live = {**env, **LIVE, "LOMBO_FAREWELL_MAX_KEEPS_PER_PERSON": "1"}
    assert cli.keep(from_env(live), "Alpha Movie", by="Ann") == 0
    assert cli.keep(from_env(live), "Alpha Movie", now=time.time() + 31 * DAY, by="Ann") == 0


def test_a_title_can_be_kept_only_so_many_times_through_the_cli(env, fake, capsys):
    fake.schedule((aid(1), 29))
    cfg = from_env({**env, **LIVE, "LOMBO_FAREWELL_MAX_KEEPS_PER_TITLE": "2"})
    assert cli.keep(cfg, "Alpha Movie") == 0
    assert cli.keep(cfg, "Alpha Movie", now=time.time() + 31 * DAY) == 0
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["kept"][aid(1)]["count"] == 2
    assert cli.keep(cfg, "Alpha Movie", now=time.time() + 62 * DAY) == 1
    assert "already kept 2 time(s), the most allowed (2)" in capsys.readouterr().err
    assert len(fake.postponed) == 2


# ---------------------------------------------------------------- webhook texts
def test_a_keep_alert_names_who_and_when_and_is_off_by_default(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie", "--by", "Ann"], {**env, **LIVE}) == 0
    assert hooks(fake) == []
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie", "--by", "Ann"], {**env, **LIVE}) == 1  # already kept: refused, no alert
    state.save(env["LOMBO_FAREWELL_STATE_FILE"], state.empty())
    assert main(["keep", "Alpha Movie", "--by", "<@Ann>"], {**env, **LIVE, "LOMBO_FAREWELL_ALERT_ON_KEEP": "true"}) == 0
    assert hooks(fake) == ["??Ann? kept Alpha Movie: it now leaves on 2027-01-15."]


def test_a_keep_alert_without_a_name_says_someone(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, **LIVE, "LOMBO_FAREWELL_ALERT_ON_KEEP": "true"}) == 0
    assert hooks(fake) == ["Someone kept Alpha Movie: it now leaves on 2027-01-15."]


def test_no_keep_alert_in_a_dry_run(env, fake):
    fake.schedule((aid(1), 29))
    assert main(["keep", "Alpha Movie"], {**env, "LOMBO_FAREWELL_ALERT_ON_KEEP": "true"}) == 0
    assert hooks(fake) == []


def test_a_failing_keep_alert_keeps_the_keep_and_warns(env, fake, capsys):
    fake.schedule((aid(1), 29))
    bad = {
        **env,
        **LIVE,
        "LOMBO_FAREWELL_ALERT_ON_KEEP": "true",
        "LOMBO_FAREWELL_WEBHOOK_URL": "http://127.0.0.1:1/hook",
    }
    assert main(["keep", "Alpha Movie"], bad) == 0
    cap = capsys.readouterr()
    assert "Kept Alpha Movie" in cap.out
    assert "[warn] keep alert failed" in cap.err


def test_deleting_soon_is_alerted_once_and_again_after_a_keep(env, fake):
    fake.schedule((aid(1), 29.5))  # about half a day left
    live = {**env, **LIVE, "LOMBO_FAREWELL_ALERT_DELETING_SOON": "true"}
    assert main(["run"], live) == 0
    assert main(["run"], live) == 0
    assert hooks(fake) == ["Alpha Movie is deleted within a day. Keep it now if someone still wants it."]
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["warned"] == [aid(1)]
    fake.schedule((aid(1), 5))  # kept: far away again, so it is forgotten
    assert main(["run"], live) == 0
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["warned"] == []
    fake.schedule((aid(1), 29.5))
    assert main(["run"], live) == 0
    assert len(hooks(fake)) == 2


def test_deleting_soon_is_off_by_default_and_silent_in_a_dry_run(env, fake):
    fake.schedule((aid(1), 29.5))
    assert main(["run"], {**env, **LIVE}) == 0
    assert main(["run"], {**env, "LOMBO_FAREWELL_ALERT_DELETING_SOON": "true"}) == 0
    assert hooks(fake) == []


def test_a_failing_deleting_soon_alert_is_retried(env, fake, capsys):
    fake.schedule((aid(1), 29.5))
    bad = {
        **env,
        **LIVE,
        "LOMBO_FAREWELL_ALERT_DELETING_SOON": "true",
        "LOMBO_FAREWELL_WEBHOOK_URL": "http://127.0.0.1:1/hook",
    }
    assert main(["run"], bad) == 0
    assert "[FAIL] webhook failed" in capsys.readouterr().err
    assert state.load(env["LOMBO_FAREWELL_STATE_FILE"])["warned"] == []


def test_deleting_soon_without_a_webhook_only_prints(env, fake, capsys):
    fake.schedule((aid(1), 29.5))
    no_hook = {k: v for k, v in env.items() if "WEBHOOK" not in k}
    assert main(["run"], {**no_hook, **LIVE, "LOMBO_FAREWELL_ALERT_DELETING_SOON": "true"}) == 0
    assert "deleting soon: Alpha Movie" in capsys.readouterr().out


def test_the_post_keep_command_also_learns_who_and_until(env, fake, monkeypatch):
    fake.schedule((aid(1), 29))
    seen = {}

    def fake_run(args, **kw):
        seen.update(json.loads(kw["env"]["LOMBO_FAREWELL_KEPT_JSON"]))
        import subprocess

        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert (
        main(["keep", "Alpha Movie", "--by", "Ann"], {**env, **LIVE, "LOMBO_FAREWELL_POST_KEEP_COMMAND": "hook"}) == 0
    )
    assert seen == {"id": aid(1), "title": "Alpha Movie", "by": "Ann", "until": 1_799_971_200}


def test_candidate_default_has_no_group_name():
    assert Candidate("a", None, None, None).group_name is None
