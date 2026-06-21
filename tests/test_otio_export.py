"""Interop tests for the OpenTimelineIO exporter (round-trip is the product)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from cutlist.models import Action, ChangeList, ChangeRequest
from cutlist.otio_export import (
    METADATA_NAMESPACE,
    build_timeline,
    to_otio,
    to_otio_string,
)
from cutlist.timecode import Timecode, TimeRange


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


def test_to_otio_string_round_trips_equal() -> None:
    changes = _changes()
    s = to_otio_string(changes)
    reloaded = otio.adapters.read_from_string(s, "otio_json")
    built = build_timeline(changes)
    assert built.to_json_string() == reloaded.to_json_string()


def test_track_named_after_title() -> None:
    tl = build_timeline(_changes())
    assert tl.name == "My Cutlist"
    assert tl.tracks[0].name == "My Cutlist"


def test_point_notes_are_markers_and_ranges_are_clips() -> None:
    tl = build_timeline(_changes())
    track = tl.tracks[0]
    clips = list(track.find_clips())
    # One range note -> one clip.
    assert len(clips) == 1
    # Two point notes -> two markers.
    assert len(track.markers) == 2


def test_metadata_survives_round_trip() -> None:
    changes = _changes()
    s = to_otio_string(changes)
    reloaded = otio.adapters.read_from_string(s, "otio_json")
    track = reloaded.tracks[0]
    clip = next(iter(track.find_clips()))
    meta = clip.metadata[METADATA_NAMESPACE]
    assert meta["action"] == "trim"
    assert meta["rationale"] == "the pause drags"
    assert meta["confidence"] == 0.95
    assert meta["source"] == "notes"
    # Markers carry metadata too.
    marker_meta = track.markers[0].metadata[METADATA_NAMESPACE]
    assert "action" in marker_meta and "rationale" in marker_meta


def test_rational_time_rate_and_frame_values() -> None:
    tl = build_timeline(_changes())
    track = tl.tracks[0]
    clip = next(iter(track.find_clips()))
    start = clip.source_range.start_time
    assert start.rate == 24.0
    # 01:00:12:00 -> 86688 frames at 24fps.
    assert start.value == 86688
    # Duration 01:00:12:00..01:00:19:12 = 180 frames.
    assert clip.source_range.duration.value == 180
    # Markers anchor at the right frame.
    marker = track.markers[0]
    assert marker.marked_range.start_time.rate == 24.0


def test_to_otio_writes_reloadable_file(tmp_path: Path) -> None:
    changes = _changes()
    out = to_otio(changes, tmp_path / "cut.otio")
    assert out.exists()
    reloaded = otio.adapters.read_from_file(str(out))
    built = build_timeline(changes)
    assert reloaded.to_json_string() == built.to_json_string()


def test_empty_changelist_raises() -> None:
    empty = ChangeList(fps=24.0)
    with pytest.raises(ValueError):
        to_otio_string(empty)
    with pytest.raises(ValueError):
        to_otio(empty, "unused.otio")


def test_determinism_identical_bytes() -> None:
    a = to_otio_string(_changes())
    b = to_otio_string(_changes())
    assert a == b
