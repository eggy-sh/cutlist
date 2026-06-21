"""Unit tests for the deterministic frame-rate-aware timecode core."""

from __future__ import annotations

import pytest

from cutlist.timecode import (
    Timecode,
    TimecodeError,
    TimeRange,
    format_timecode,
    parse_timecode,
)


def test_parse_round_trips_to_frame_and_back() -> None:
    tc = parse_timecode("01:00:05:00", 24.0)
    # 1h = 3600s, +5s -> 3605s * 24fps = 86520 frames.
    assert tc.frame == 86520
    assert tc.fps == 24.0
    assert tc.to_string() == "01:00:05:00"


def test_format_is_inverse_of_parse_for_a_sweep() -> None:
    for fps in (24.0, 25.0, 30.0):
        for frame in (0, 1, 23, 24, 100, 86399, 86520, 123456):
            s = format_timecode(frame, fps)
            assert parse_timecode(s, fps).frame == frame
            # And string round-trips through parse->frame->format.
            assert format_timecode(parse_timecode(s, fps).frame, fps) == s


def test_frames_field_at_or_above_fps_raises() -> None:
    with pytest.raises(TimecodeError):
        parse_timecode("00:00:00:24", 24.0)
    # 23 is valid at 24fps.
    assert parse_timecode("00:00:00:23", 24.0).frame == 23


def test_drop_frame_separator_rejected() -> None:
    with pytest.raises(TimecodeError, match="drop-frame"):
        parse_timecode("01:00:00;00", 30.0)
    with pytest.raises(TimecodeError, match="drop-frame"):
        parse_timecode("01:00:00,00", 30.0)


def test_malformed_strings_raise() -> None:
    for bad in ("nonsense", "01:00:00", "1:2:3:4:5", "", "01-00-00-00"):
        with pytest.raises(TimecodeError):
            parse_timecode(bad, 24.0)


def test_out_of_range_minutes_and_seconds_raise() -> None:
    with pytest.raises(TimecodeError):
        parse_timecode("00:60:00:00", 24.0)
    with pytest.raises(TimecodeError):
        parse_timecode("00:00:60:00", 24.0)


def test_non_string_input_raises() -> None:
    with pytest.raises(TimecodeError):
        Timecode.from_string(12345, 24.0)  # type: ignore[arg-type]


def test_negative_frame_and_non_positive_fps_raise() -> None:
    with pytest.raises(TimecodeError):
        format_timecode(-1, 24.0)
    with pytest.raises(TimecodeError):
        format_timecode(0, 0.0)
    with pytest.raises(TimecodeError):
        format_timecode(0, -24.0)
    with pytest.raises(TimecodeError):
        parse_timecode("00:00:00:00", 0.0)


def test_seconds_property() -> None:
    tc = parse_timecode("00:00:10:00", 24.0)
    assert tc.seconds == pytest.approx(10.0)
    assert parse_timecode("00:00:00:12", 24.0).seconds == pytest.approx(0.5)


def test_add_advances_by_frames() -> None:
    tc = parse_timecode("00:00:00:00", 24.0)
    assert (tc + 24).to_string() == "00:00:01:00"
    assert (tc + 25).frame == 25


def test_add_non_int_returns_notimplemented() -> None:
    tc = parse_timecode("00:00:00:00", 24.0)
    with pytest.raises(TypeError):
        _ = tc + 1.5  # type: ignore[operator]


def test_subtract_int_and_timecode() -> None:
    a = parse_timecode("00:00:02:00", 24.0)
    b = parse_timecode("00:00:01:00", 24.0)
    assert (a - 24).to_string() == "00:00:01:00"
    diff = a - b
    assert isinstance(diff, Timecode)
    assert diff.frame == 24
    assert diff.fps == 24.0


def test_subtract_mixed_fps_raises() -> None:
    a = parse_timecode("00:00:02:00", 24.0)
    b = parse_timecode("00:00:01:00", 25.0)
    with pytest.raises(TimecodeError):
        _ = a - b


def test_subtract_unsupported_type_returns_notimplemented() -> None:
    a = parse_timecode("00:00:02:00", 24.0)
    with pytest.raises(TypeError):
        _ = a - 1.5  # type: ignore[operator]


def test_ordering_within_same_fps() -> None:
    a = parse_timecode("00:00:01:00", 24.0)
    b = parse_timecode("00:00:02:00", 24.0)
    assert a < b
    assert b > a
    assert a <= a
    assert sorted([b, a]) == [a, b]


def test_timerange_duration_and_construction() -> None:
    tr = TimeRange.from_strings("01:00:05:00", "01:00:09:12", 24.0)
    # 4s12f at 24fps = 4*24 + 12 = 108 frames.
    assert tr.duration_frames == 108
    assert tr.fps == 24.0
    assert tr.start.frame < tr.end.frame


def test_timerange_equal_endpoints_allowed() -> None:
    tr = TimeRange.from_strings("01:00:05:00", "01:00:05:00", 24.0)
    assert tr.duration_frames == 0


def test_timerange_end_before_start_raises() -> None:
    with pytest.raises(TimecodeError):
        TimeRange.from_strings("01:00:09:00", "01:00:05:00", 24.0)


def test_timerange_mixed_fps_raises() -> None:
    start = parse_timecode("01:00:05:00", 24.0)
    end = parse_timecode("01:00:09:00", 25.0)
    with pytest.raises(TimecodeError):
        TimeRange(start=start, end=end)
