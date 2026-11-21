from conftest import DAY, NOW
from lombo_farewell import core
from lombo_farewell.core import Candidate, Entry


def cand(delete_at, grace=10.0, item="a", group="1"):
    return Candidate(item, None, delete_at, grace, group, item)


def plan(items, can_postpone=True):
    return core.plan([(c, c.item_id or "", t) for t, c in items], NOW, can_postpone)


def test_norm_id_strips_dashes_and_case():
    assert core.norm_id("AB-cd") == "abcd"
    assert core.norm_id(None) == ""


def test_days_left_overdue_and_can_keep_match_the_original_rules():
    by = {
        e.item_id: e
        for e in plan(
            [
                ("A", cand(NOW - 3 * DAY, item="a")),  # 3 days late
                ("B", cand(NOW + 1 * DAY, item="b")),  # 1 day left, inside its wait
                ("C", cand(NOW - 0.5 * DAY, item="c")),  # late by less than a day
                ("D", cand(NOW + 40 * DAY, item="d")),  # postponed already: far beyond the normal wait
            ]
        )
    }
    assert (by["a"].overdue_days, by["a"].days_left) == (3, 0)
    assert (by["b"].overdue_days, by["b"].days_left) == (0, 1)
    assert by["c"].overdue_days == 0
    assert by["a"].can_keep
    assert by["b"].can_keep
    assert not by["d"].can_keep


def test_sorted_soonest_first_unknown_dates_last_and_one_entry_per_title():
    out = plan(
        [
            ("Late", cand(NOW + 9 * DAY, item="x")),
            ("None", cand(None, item="n")),
            ("Soon", cand(NOW + 2 * DAY, item="y")),
            ("Dup", cand(NOW + 1 * DAY, item="x")),  # same title, sooner: it wins
        ]
    )
    assert [e.item_id for e in out] == ["x", "y", "n"]
    assert out[0].title == "Dup"
    assert out[2].days_left is None


def test_no_postpone_support_or_no_grace_means_no_keep():
    assert not plan([("A", cand(NOW + DAY))], can_postpone=False)[0].can_keep
    assert not plan([("A", cand(NOW + DAY, grace=None))])[0].can_keep
    assert not plan([("A", cand(None))])[0].can_keep


def test_entries_without_an_id_are_dropped_and_missing_title_falls_back_to_id():
    assert core.plan([(cand(NOW), "", "x")], NOW, True) == []
    assert core.plan([(cand(NOW), "AB-CD", "")], NOW, True)[0].title == "abcd"


def entry(title, item, can_keep=True):
    return Entry(item, title, NOW + DAY, 1, 0, can_keep)


def test_str_forms():
    assert "1 day(s) left" in str(entry("T", "t"))
    assert "overdue" in str(Entry("t", "T", NOW - 3 * DAY, 0, 3, True))
    assert "date unknown" in str(Entry("t", "T", None, None, 0, False))


def test_diff_adds_in_order_and_removes_stale():
    d = core.diff({"a", "z"}, ["b", "a", "c", "b"], 25)
    assert d.add == ("b", "c")
    assert d.remove == ("z",)
    assert d.held_back == ()
    assert d.changes
    assert not core.diff({"a"}, ["a"], 5).changes


def test_diff_caps_removals_so_an_empty_source_cannot_empty_the_collection():
    have = {f"i{n}" for n in range(10)}
    d = core.diff(have, [], 3)
    assert len(d.remove) == 3
    assert len(d.held_back) == 7
    assert set(d.remove) | set(d.held_back) == have


def test_find_exact_partial_id_and_failures():
    es = [
        entry("Alpha Movie", "a1"),
        entry("Beta Show", "b2"),
        entry("Beta Show 2", "b3"),
        entry("Twin", "t1"),
        entry("twin", "t2"),
    ]
    assert core.find(es, "alpha movie")[0] is es[0]
    assert core.find(es, "alpha")[0] is es[0]
    assert core.find(es, "B2")[0] is es[1]
    assert core.find(es, "Beta Show")[0] is es[1]  # exact beats partial
    assert "be more exact" in core.find(es, "beta")[1]
    assert "use the Jellyfin id" in core.find(es, "twin")[1]
    assert "not in the leaving soon list" in core.find(es, "zzz")[1]
    assert core.find(es, "  ")[1] == "no title given"


def test_keep_check():
    assert core.keep_check(None) == "not-leaving"
    assert core.keep_check(entry("T", "t", can_keep=False)) == "already-kept"
    assert core.keep_check(entry("T", "t")) == "ok"


def test_overdue_alerts_once_and_forget_titles_that_left():
    late = lambda i, d: Entry(i, i, NOW, 0, d, True)  # noqa: E731
    es = [late("a", 2), late("b", 1)]
    to_report, remembered = core.overdue_to_alert(es, set())
    assert [e.item_id for e in to_report] == ["a"]
    assert remembered == {"a"}
    assert core.overdue_to_alert(es, remembered)[0] == []
    to_report, remembered = core.overdue_to_alert([], remembered)
    assert remembered == set()  # gone: a return would alert again
    assert [e.item_id for e in core.overdue_to_alert([late("a", 5)], remembered)[0]] == ["a"]
