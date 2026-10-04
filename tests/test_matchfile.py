import json

import pytest

from tennis_to_utube import matchfile as mfmod
from tennis_to_utube.matchfile import (
    Event, MatchFile, MatchFileError, Source, UnsupportedVersionError, load, loads, save,
)

SAMPLE = {
    "format_version": 1,
    "sources": [{"path": "GX010008.MP4", "duration_ms": 2271000, "codec": "hevc", "width": 3840,
                 "height": 2160, "fps": "60000/1001", "pix_fmt": "yuvj420p",
                 "future_source_field": {"x": 1}}],
    "match": {"kind": "singles", "sides": {"A": {"players": ["Emma"], "role": "ours"},
                                          "B": {"players": ["Sara"], "role": "opponent"}},
              "venue": "Court 3"},
    "settings": {"lead_in_ms": {"default": 5000}, "chapter_gap_ms": 600000, "unknown_setting": True},
    "events": [
        {"id": "e_0001", "t_ms": 734512, "type": "game_start", "side": "A", "player": "Emma",
         "result": None, "observed": None, "called": None, "source": "human", "confidence": None,
         "inferred": False, "tags": [], "details": {}, "note": "", "ai_trace": [1, 2, 3]},
        {"id": "e_0002", "t_ms": 740000, "type": "point", "result": "B", "tags": ["close"],
         "details": {"direction": "long", "margin_cm": 6}},
    ],
    "youtube": {"video_id": None, "playlist": "PL123"},
    "next_event_seq": 3,
    "from_the_future": {"nested": [1, {"a": None}]},
}


def test_round_trip_keeps_unknown_fields():
    mf = loads(json.dumps(SAMPLE))
    out = json.loads(mfmod.dumps(mf))
    assert out["from_the_future"] == SAMPLE["from_the_future"]
    assert out["sources"][0]["future_source_field"] == {"x": 1}
    assert out["events"][0]["ai_trace"] == [1, 2, 3]
    assert out["match"]["venue"] == "Court 3"
    assert out["settings"]["unknown_setting"] is True
    assert out["youtube"]["playlist"] == "PL123"
    # A second round trip is byte-identical.
    assert mfmod.dumps(loads(mfmod.dumps(mf))) == mfmod.dumps(mf)


def test_known_fields_are_typed():
    mf = loads(json.dumps(SAMPLE))
    assert mf.sources[0].duration_ms == 2271000
    e = mf.events[1]
    assert e.t_ms == 740000 and e.result == "B" and e.source == "human"
    assert e.details["margin_cm"] == 6


def test_names_kept_exactly_as_typed(tmp_path):
    mf = MatchFile()
    mf.match["sides"]["A"]["players"] = ["Zoë", "Ana-María"]
    mf.add_event(1000, "game_start", player="Zoë")
    save(mf, tmp_path / "m.match.json")
    again = load(tmp_path / "m.match.json")
    assert again.match["sides"]["A"]["players"] == ["Zoë", "Ana-María"]
    assert again.events[0].player == "Zoë"
    assert "Zoë" in (tmp_path / "m.match.json").read_text(encoding="utf-8")


def test_newer_version_refused():
    with pytest.raises(UnsupportedVersionError):
        loads(json.dumps({**SAMPLE, "format_version": 2}))


@pytest.mark.parametrize("bad", [
    {"sources": []},  # no format_version
    {**SAMPLE, "events": [{"id": "e_1", "t_ms": "soon", "type": "point"}]},
    {**SAMPLE, "events": [{"id": "e_1", "t_ms": -5, "type": "point"}]},
    {**SAMPLE, "events": [{"id": "e_1", "t_ms": 1, "type": "point"},
                          {"id": "e_1", "t_ms": 2, "type": "point"}]},
    {**SAMPLE, "sources": [{"path": "a.mp4"}]},
    {**SAMPLE, "events": {}},
])
def test_invalid_files_rejected(bad):
    with pytest.raises(MatchFileError):
        loads(json.dumps(bad))


def test_invalid_json_rejected():
    with pytest.raises(MatchFileError):
        loads("{not json")


def test_event_ids_never_reused(tmp_path):
    mf = MatchFile()
    a = mf.add_event(1, "point")
    b = mf.add_event(2, "point")
    mf.remove_event(b.id)
    path = tmp_path / "m.match.json"
    save(mf, path)
    mf2 = load(path)
    c = mf2.add_event(3, "point")
    assert (a.id, b.id, c.id) == ("e_0001", "e_0002", "e_0003")


def test_next_seq_recovered_when_missing():
    data = {**SAMPLE}
    del data["next_event_seq"]
    data["events"] = [{"id": "e_0041", "t_ms": 1, "type": "point"}]
    mf = loads(json.dumps(data))
    assert mf.add_event(5, "point").id == "e_0042"


def test_save_is_atomic_and_keeps_backup(tmp_path):
    path = tmp_path / "GX010008.match.json"
    mf = MatchFile()
    mf.add_event(1, "match_start")
    save(mf, path)
    assert not (tmp_path / "GX010008.match.json.bak").exists()
    mf.add_event(2, "game_start")
    save(mf, path)
    backup = load(tmp_path / "GX010008.match.json.bak")
    assert [e.type for e in backup.events] == ["match_start"]
    assert [e.type for e in load(path).events] == ["match_start", "game_start"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["GX010008.match.json", "GX010008.match.json.bak"]


def test_observed_called_default_to_each_other():
    e = Event("e", 1, "point", called="out")
    assert e.effective_observed == "out" and e.effective_called == "out"
    e = Event("e", 1, "point", observed="in")
    assert e.effective_called == "in"
    e = Event("e", 1, "point", observed="in", called="out")
    assert (e.effective_observed, e.effective_called) == ("in", "out")
    # Stored data is not filled in.
    assert Event("e", 1, "point", called="out").to_dict()["observed"] is None


def test_null_collections_become_empty():
    mf = loads(json.dumps({**SAMPLE, "events": [
        {"id": "e_1", "t_ms": 1, "type": "point", "tags": None, "details": None, "note": None}]}))
    assert mf.events[0].tags == [] and mf.events[0].details == {} and mf.events[0].note == ""


def test_sorted_events_stable():
    mf = MatchFile()
    mf.add_event(500, "point")
    first = mf.add_event(100, "game_start")
    second = mf.add_event(100, "serve_in")
    assert [e.id for e in mf.sorted_events()][:2] == [first.id, second.id]


def test_paths_relative_to_match_file(tmp_path):
    video = tmp_path / "match" / "GX010008.MP4"
    video.parent.mkdir()
    video.touch()
    match_path = mfmod.default_match_path(video)
    assert match_path.name == "GX010008.match.json"
    assert mfmod.relative_source_path(video, match_path) == "GX010008.MP4"
    other = tmp_path / "elsewhere" / "GX020008.MP4"
    rel = mfmod.relative_source_path(other, match_path)
    assert rel == "../elsewhere/GX020008.MP4"
    assert mfmod.resolve_source_path(rel, match_path) == other.resolve()
    assert mfmod.resolve_source_path(str(video.resolve()), match_path) == video.resolve()


def test_source_dict_order_and_extra():
    s = Source("a.mp4", 1000, extra={"z": 1})
    assert list(s.to_dict())[:2] == ["path", "duration_ms"] and s.to_dict()["z"] == 1


def test_output_field_round_trips():
    mf = loads(json.dumps({**SAMPLE, "output": {"path": "out.mp4", "segments": [[0, 5, 0]]}}))
    assert mf.output["path"] == "out.mp4"
    assert json.loads(mfmod.dumps(mf))["output"]["segments"] == [[0, 5, 0]]
    with pytest.raises(MatchFileError):
        loads(json.dumps({**SAMPLE, "output": [1]}))
