"""Interop + golden tests for the CMX3600 EDL exporter."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from cutlist.edl_export import DEFAULT_REEL, _format_event, build_edl, to_edl
from cutlist.models import Action, ChangeList, ChangeRequest
from cutlist.timecode import Timecode, TimeRange

#: The exact expected EDL for ``_changes()`` below. Byte-level golden: any drift
#: in column layout, header, numbering, or comment lines must be intentional.
GOLDEN_EDL = (
    "TITLE: My Cutlist\n"
    "FCM: NON-DROP FRAME\n"
    "\n"
    "001  AX      V     C        "
    "01:00:05:00 01:00:05:01 01:00:05:00 01:00:05:01\n"
    "* TRIM (confidence 0.90)\n"
    "* punch in on the speaker\n"
    "* SOURCE: notes\n"
    "\n"
    "002  AX      V     C        "
    "01:00:12:00 01:00:19:12 01:00:12:00 01:00:19:12\n"
    "* TRIM (confidence 0.95)\n"
    "* the pause drags\n"
    "* SOURCE: notes\n"
    "\n"
    "003  AX      V     C        "
    "01:00:30:00 01:00:30:01 01:00:30:00 01:00:30:01\n"
    "* FLAG (confidence 0.80)\n"
    "* lower-third misspelled\n"
    "* SOURCE: c-101\n"
)


def _changes(fps: float = 24.0) -> ChangeList:
    reqs = [
        ChangeRequest(
            action=Action.TRIM,
            rationale="punch in on the speaker",
            at=Timecode.from_string("01:00:05:00", fps),
            confidence=0.9,
            source="notes",
        ),
        ChangeRequest(
            action=Action.TRIM,
            rationale="the pause drags",
            span=TimeRange.from_strings("01:00:12:00", "01:00:19:12", fps),
            confidence=0.95,
            source="notes",
        ),
        ChangeRequest(
            action=Action.FLAG,
            rationale="lower-third misspelled",
            at=Timecode.from_string("01:00:30:00", fps),
            confidence=0.8,
            source="c-101",
        ),
    ]
    return ChangeList.from_requests(reqs, fps, title="My Cutlist")


def test_build_edl_matches_golden() -> None:
    assert build_edl(_changes()) == GOLDEN_EDL


def test_build_edl_has_header_lines() -> None:
    edl = build_edl(_changes())
    assert edl.startswith("TITLE: My Cutlist\n")
    assert "FCM: NON-DROP FRAME\n" in edl


def test_events_numbered_in_sorted_order() -> None:
    # Provide requests out of order; they must be renumbered 001.. in time order.
    reqs = [
        ChangeRequest(
            action=Action.FLAG,
            rationale="late",
            at=Timecode.from_string("01:00:30:00", 24.0),
        ),
        ChangeRequest(
            action=Action.CUT,
            rationale="early",
            at=Timecode.from_string("01:00:05:00", 24.0),
        ),
    ]
    edl = build_edl(ChangeList.from_requests(reqs, 24.0, title="t"))
    early_pos = edl.index("early")
    late_pos = edl.index("late")
    assert early_pos < late_pos
    assert "001  " in edl
    assert "002  " in edl
    # 001 anchors the early note.
    assert edl.index("001  ") < early_pos


def test_rationale_present_as_comment() -> None:
    edl = build_edl(_changes())
    assert "* punch in on the speaker\n" in edl
    assert "* the pause drags\n" in edl


def test_parses_via_cmx_3600_with_correct_events() -> None:
    fps = 24.0
    edl = build_edl(_changes(fps))
    timeline = otio.adapters.read_from_string(edl, "cmx_3600", rate=fps)
    clips = list(timeline.tracks[0].find_clips())
    assert len(clips) == 3
    starts = [c.source_range.start_time.value for c in clips]
    # 01:00:05:00=86520, 01:00:12:00=86688, 01:00:30:00=87120
    assert starts == [86520, 86688, 87120]
    durations = [c.source_range.duration.value for c in clips]
    # point=1, range 12:00..19:12 = 180, point=1
    assert durations == [1, 180, 1]


def test_point_notes_emit_one_frame_events() -> None:
    fps = 24.0
    ev = _format_event(
        1,
        ChangeRequest(
            action=Action.FLAG,
            rationale="x",
            at=Timecode.from_string("01:00:05:00", fps),
        ),
        fps,
    )
    # 1-frame source: 01:00:05:00 -> 01:00:05:01
    assert "01:00:05:00 01:00:05:01" in ev
    assert ev.startswith("001  ")
    assert DEFAULT_REEL in ev


def test_format_event_range() -> None:
    fps = 24.0
    ev = _format_event(
        7,
        ChangeRequest(
            action=Action.TRIM,
            rationale="tighten",
            span=TimeRange.from_strings("01:00:12:00", "01:00:19:12", fps),
            confidence=0.5,
        ),
        fps,
    )
    assert ev.startswith("007  ")
    assert "01:00:12:00 01:00:19:12" in ev
    assert "* TRIM (confidence 0.50)" in ev


def test_zero_length_span_emits_one_frame_event() -> None:
    fps = 24.0
    ev = _format_event(
        1,
        ChangeRequest(
            action=Action.FLAG,
            rationale="marker",
            span=TimeRange.from_strings("01:00:05:00", "01:00:05:00", fps),
        ),
        fps,
    )
    # Degenerate zero-length range still yields a visible 1-frame event.
    assert "01:00:05:00 01:00:05:01" in ev


def test_empty_rationale_omits_comment_line() -> None:
    fps = 24.0
    ev = _format_event(
        1,
        ChangeRequest(
            action=Action.FLAG,
            rationale="   ",
            at=Timecode.from_string("01:00:05:00", fps),
        ),
        fps,
    )
    lines = ev.splitlines()
    # action annotation + source comment, but no blank/whitespace rationale line.
    comment_lines = [line for line in lines if line.startswith("* ")]
    assert any("FLAG" in line for line in comment_lines)
    assert any("SOURCE" in line for line in comment_lines)
    # No empty rationale comment ("* " followed by nothing meaningful).
    assert "* \n" not in ev


def test_to_edl_writes_file(tmp_path: Path) -> None:
    out = to_edl(_changes(), tmp_path / "cut.edl")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text == GOLDEN_EDL
    # Re-readable by cmx_3600.
    timeline = otio.adapters.read_from_file(str(out), rate=24.0)
    assert len(list(timeline.tracks[0].find_clips())) == 3


def test_empty_changelist_raises() -> None:
    empty = ChangeList(fps=24.0)
    with pytest.raises(ValueError):
        build_edl(empty)
    with pytest.raises(ValueError):
        to_edl(empty, "unused.edl")


def test_determinism_identical_bytes() -> None:
    assert build_edl(_changes()) == build_edl(_changes())
