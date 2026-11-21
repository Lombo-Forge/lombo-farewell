import json

import pytest

from conftest import DAY, NOW, aid, iso
from lombo_farewell import sources
from lombo_farewell.sources import FileSource, MaintainerrSource, SourceError


def test_parse_when_forms():
    assert sources.parse_when(5) == 5.0
    assert sources.parse_when("2026-12-01") == 1796083200.0
    assert sources.parse_when("2026-12-01T00:00:00Z") == 1796083200.0
    assert sources.parse_when("2026-12-01T00:00:00") == 1796083200.0
    for bad in ("soon", True, ""):
        with pytest.raises(ValueError):
            sources.parse_when(bad)


def test_maintainerr_candidates_follow_the_original_rules():
    cols = [
        {"id": 1, "isActive": True, "deleteAfterDays": 10},
        {"id": 2, "isActive": False, "deleteAfterDays": 10},  # inactive: ignored
        {"id": 3, "isActive": True, "deleteAfterDays": None},  # no wait: ignored
        {"id": 4, "isActive": True, "deleteAfterDays": True},
        "junk",
    ]
    media = {
        1: [
            {"mediaServerId": "a", "addDate": iso(NOW)},
            {"mediaServerId": "", "addDate": iso(NOW)},  # no id
            {"mediaServerId": "b"},  # no date
            {"mediaServerId": "c", "addDate": "garbage"},
            "junk",
        ],
        2: [{"mediaServerId": "z", "addDate": iso(NOW)}],
    }
    out = sources.maintainerr_candidates(cols, media)
    assert len(out) == 1
    assert out[0].item_id == "a"
    assert out[0].delete_at == NOW + 10 * DAY
    assert out[0].grace_days == 10.0


def test_maintainerr_candidates_refuse_odd_shapes():
    with pytest.raises(SourceError, match="not a list"):
        sources.maintainerr_candidates({}, {})
    with pytest.raises(SourceError, match="media list"):
        sources.maintainerr_candidates([{"id": 1, "isActive": True, "deleteAfterDays": 5}], {1: {}})


def test_maintainerr_source_fetch_and_postpone(fake):
    fake.schedule((aid(1), 29), (aid(2), 5))
    src = MaintainerrSource(fake.url)
    assert [c.item_id for c in src.fetch()] == [aid(1), aid(2)]
    until = src.postpone("7", aid(1), 30)
    assert until == sources.parse_when("2027-01-15T00:00:00Z")
    assert fake.postponed == [{"collectionId": 7, "mediaId": aid(1), "days": 30}]


def test_maintainerr_errors_become_source_errors(fake):
    fake.maint_status = 500
    with pytest.raises(SourceError, match="HTTP 500"):
        MaintainerrSource(fake.url).fetch()
    fake.maint_status = 200
    fake.maint_collections = {"not": "a list"}
    with pytest.raises(SourceError, match="did not return a list"):
        MaintainerrSource(fake.url).fetch()
    with pytest.raises(SourceError, match="Maintainerr"):
        MaintainerrSource("http://127.0.0.1:1", timeout=1).fetch()
    fake.postpone_reply = None
    with pytest.raises(SourceError, match="deletionDate"):
        MaintainerrSource(fake.url).postpone("x", aid(1), 5)


def test_parse_plain_text_list():
    text = "# comment\n\nAlpha Movie | 2026-12-01\nBeta Show\n"
    a, b = sources.parse_list(text)
    assert (a.title, a.delete_at, a.grace_days) == ("Alpha Movie", 1796083200.0, None)
    assert (b.title, b.delete_at) == ("Beta Show", None)


def test_parse_json_list_and_object_form():
    data = [{"title": "A", "leaves": "2026-12-01"}, {"id": "abc"}]
    for doc in (data, {"items": data}):
        a, b = sources.parse_list(json.dumps(doc))
        assert (a.title, a.item_id) == ("A", None)
        assert (b.item_id, b.title) == ("abc", None)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[1, 2]", "not an object"),
        ('[{"leaves": "2026-12-01"}]', "no title or id"),
        ('[{"title": "A", "leaves": "someday"}]', "not a date"),
        ("[{bad", "not valid JSON"),
        ('{"other": 1}', "must be an array"),
        (" | 2026-12-01", "no title or id"),
        ("A | someday", "line 1"),
    ],
)
def test_a_bad_list_is_an_error_not_a_partial_list(text, message):
    with pytest.raises(SourceError, match=message):
        sources.parse_list(text)


def test_file_source_reads_and_refuses_to_postpone(tmp_path):
    f = tmp_path / "leaving.txt"
    f.write_text("Alpha Movie | 2026-12-01\n")
    src = FileSource(str(f))
    assert [c.title for c in src.fetch()] == ["Alpha Movie"]
    with pytest.raises(SourceError, match="cannot postpone"):
        src.postpone("g", "m", 30)
    assert src.can_postpone is False


def test_a_missing_file_is_an_error_not_an_empty_list(tmp_path):
    with pytest.raises(SourceError, match="cannot read"):
        FileSource(str(tmp_path / "nope.txt")).fetch()
    (tmp_path / "bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(SourceError, match="cannot read"):
        FileSource(str(tmp_path / "bin")).fetch()
