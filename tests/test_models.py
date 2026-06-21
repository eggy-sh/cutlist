"""Unit tests for the Action / ChangeRequest / ChangeList records."""

from __future__ import annotations

import pytest

from cutlist.models import Action, ChangeList, ChangeRequest
from cutlist.timecode import Timecode, TimeRange


def test_action_coerce_member_value() -> None:
    assert Action.coerce("cut") is Action.CUT
    assert Action.coerce("TRIM") is Action.TRIM
    assert Action.coerce("Color") is Action.COLOR


def test_action_coerce_name() -> None:
    assert Action.coerce("VFX") is Action.VFX
    assert Action.coerce("flag") is Action.FLAG


def test_action_coerce_synonyms() -> None:
    assert Action.coerce("delete") is Action.CUT
    assert Action.coerce("remove") is Action.CUT
    assert Action.coerce("tighten") is Action.TRIM
    assert Action.coerce("shorten") is Action.TRIM
    assert Action.coerce("add") is Action.INSERT


def test_action_coerce_unknown_is_other_and_never_raises() -> None:
    assert Action.coerce("zorptastic") is Action.OTHER
    assert Action.coerce("") is Action.OTHER
    assert Action.coerce("   ") is Action.OTHER
    assert Action.coerce(None) is Action.OTHER  # type: ignore[arg-type]


def test_action_coerce_passthrough_member() -> None:
    assert Action.coerce(Action.AUDIO) is Action.AUDIO


def test_action_strenum_value_equality() -> None:
    assert Action.CUT == "cut"
    assert Action.TRIM.value == "trim"


def _point(fps: float = 24.0) -> Timecode:
    return Timecode.from_string("01:00:05:00", fps)


def _span(fps: float = 24.0) -> TimeRange:
    return TimeRange.from_strings("01:00:05:00", "01:00:09:00", fps)


def test_change_request_point_ok() -> None:
    cr = ChangeRequest(action=Action.FLAG, rationale="r", at=_point())
    assert cr.at is not None
    assert cr.fps == 24.0
    assert cr.start_frame == _point().frame


def test_change_request_range_ok() -> None:
    cr = ChangeRequest(action=Action.TRIM, rationale="r", span=_span())
    assert cr.span is not None
    assert cr.start_frame == _span().start.frame


def test_change_request_neither_anchor_raises() -> None:
    with pytest.raises(ValueError):
        ChangeRequest(action=Action.CUT, rationale="r")


def test_change_request_both_anchors_raise() -> None:
    with pytest.raises(ValueError):
        ChangeRequest(action=Action.CUT, rationale="r", at=_point(), span=_span())


def test_change_request_confidence_bounds() -> None:
    ChangeRequest(action=Action.CUT, rationale="r", at=_point(), confidence=0.0)
    ChangeRequest(action=Action.CUT, rationale="r", at=_point(), confidence=1.0)
    with pytest.raises(ValueError):
        ChangeRequest(action=Action.CUT, rationale="r", at=_point(), confidence=-0.1)
    with pytest.raises(ValueError):
        ChangeRequest(action=Action.CUT, rationale="r", at=_point(), confidence=1.1)


def test_change_request_to_from_dict_point() -> None:
    cr = ChangeRequest(
        action=Action.FLAG,
        rationale="title misspelled",
        at=_point(),
        confidence=0.8,
        source="c-101",
    )
    d = cr.to_dict()
    assert d["action"] == "flag"
    assert d["at"] == "01:00:05:00"
    assert "start" not in d and "end" not in d
    back = ChangeRequest.from_dict(d, 24.0)
    assert back == cr


def test_change_request_to_from_dict_range() -> None:
    cr = ChangeRequest(
        action=Action.TRIM,
        rationale="pause drags",
        span=_span(),
        confidence=0.95,
    )
    d = cr.to_dict()
    assert d["start"] == "01:00:05:00"
    assert d["end"] == "01:00:09:00"
    assert "at" not in d
    back = ChangeRequest.from_dict(d, 24.0)
    assert back == cr


def test_changelist_len_and_iter() -> None:
    reqs = [
        ChangeRequest(action=Action.FLAG, rationale="a", at=_point()),
        ChangeRequest(action=Action.TRIM, rationale="b", span=_span()),
    ]
    cl = ChangeList.from_requests(reqs, 24.0)
    assert len(cl) == 2
    assert list(cl) == reqs


def test_changelist_from_requests_fps_mismatch_raises() -> None:
    bad = ChangeRequest(action=Action.FLAG, rationale="x", at=_point(25.0))
    with pytest.raises(ValueError):
        ChangeList.from_requests([bad], 24.0)


def test_changelist_sorted_orders_by_start_frame() -> None:
    late = ChangeRequest(
        action=Action.FLAG, rationale="late", at=Timecode.from_string("01:00:30:00", 24.0)
    )
    early = ChangeRequest(
        action=Action.FLAG, rationale="early", at=Timecode.from_string("01:00:05:00", 24.0)
    )
    mid = ChangeRequest(
        action=Action.TRIM,
        rationale="mid",
        span=TimeRange.from_strings("01:00:12:00", "01:00:19:00", 24.0),
    )
    cl = ChangeList.from_requests([late, early, mid], 24.0).sorted()
    assert [r.rationale for r in cl] == ["early", "mid", "late"]


def test_changelist_to_from_dict_round_trip() -> None:
    reqs = [
        ChangeRequest(action=Action.FLAG, rationale="a", at=_point(), confidence=0.8),
        ChangeRequest(action=Action.TRIM, rationale="b", span=_span(), confidence=0.95),
    ]
    cl = ChangeList.from_requests(reqs, 24.0, title="my notes")
    d = cl.to_dict()
    assert d["title"] == "my notes"
    assert d["fps"] == 24.0
    assert len(d["requests"]) == 2
    back = ChangeList.from_dict(d)
    assert back.title == cl.title
    assert back.fps == cl.fps
    assert back.requests == cl.requests
