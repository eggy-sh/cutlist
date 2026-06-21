"""Deterministic, frame-rate-aware SMPTE timecode parsing and formatting.

A timecode is ``HH:MM:SS:FF`` (frames in the last field), interpreted against a
known frame rate (``fps``). This module is **pure** and has no third-party
dependencies, so it is the deterministic bedrock the extractor and both
exporters build on: the same string + fps always yields the same frame count.

Drop-frame timecode (``;`` separator before the frames field) is recognized and
rejected with a clear :class:`TimecodeError` in v0.1 — non-drop-frame only —
rather than being silently mis-parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A canonical non-drop ``HH:MM:SS:FF`` timecode. All four fields are required and
#: separated by ``:``. A ``;`` anywhere (drop-frame) is matched separately and
#: rejected, so it never falls through to a confusing "malformed" error.
_TIMECODE_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{1,2}):(\d{1,3})$")
_DROP_FRAME_RE = re.compile(r"[;,]")


class TimecodeError(ValueError):
    """Raised on malformed timecode, out-of-range frames, or unsupported fps."""


def _fps_to_int(fps: float) -> int:
    """Frames-per-second rounded to the nearest whole frame for field math.

    Non-drop timecode counts whole frames per second, so the frames field cap is
    ``ceil``-free: 23.976 and 24 both cap the frames field at 24 (a frame index of
    0..23). We round to nearest to tolerate the common fractional NTSC rates.
    """
    return int(round(fps))


@dataclass(frozen=True, order=True)
class Timecode:
    """A frame-accurate point in time: a frame count at a given frame rate.

    The canonical representation is an integer ``frame`` index (0-based) plus the
    ``fps`` it was measured against. ``HH:MM:SS:FF`` strings are derived views.
    Two ``Timecode`` objects are only comparable/arithmetic-compatible when they
    share an ``fps``; mixing rates raises :class:`TimecodeError`.
    """

    frame: int
    fps: float

    @classmethod
    def from_string(cls, text: str, fps: float) -> Timecode:
        """Parse an ``HH:MM:SS:FF`` string into a :class:`Timecode` at ``fps``.

        Raises :class:`TimecodeError` if ``text`` is not well-formed, if the
        frames field is >= ``fps``, or if any field is out of range.
        """
        if fps <= 0:
            raise TimecodeError(f"fps must be positive, got {fps!r}")
        if not isinstance(text, str):
            raise TimecodeError(f"timecode must be a string, got {type(text).__name__}")
        stripped = text.strip()
        if _DROP_FRAME_RE.search(stripped):
            raise TimecodeError(
                f"drop-frame timecode is not supported: {text!r} "
                "(non-drop-frame ':' separators only)"
            )
        match = _TIMECODE_RE.match(stripped)
        if match is None:
            raise TimecodeError(f"malformed timecode {text!r}; expected HH:MM:SS:FF")
        hours, minutes, seconds, frames = (int(g) for g in match.groups())
        fps_int = _fps_to_int(fps)
        if minutes >= 60:
            raise TimecodeError(f"minutes field out of range in {text!r}")
        if seconds >= 60:
            raise TimecodeError(f"seconds field out of range in {text!r}")
        if frames >= fps_int:
            raise TimecodeError(f"frames field {frames} >= fps {fps_int} in {text!r}")
        total = ((hours * 60 + minutes) * 60 + seconds) * fps_int + frames
        return cls(frame=total, fps=fps)

    def to_string(self) -> str:
        """Render this timecode back to canonical ``HH:MM:SS:FF`` form."""
        return format_timecode(self.frame, self.fps)

    @property
    def seconds(self) -> float:
        """The wall-clock offset of this timecode in seconds (``frame / fps``)."""
        return self.frame / self.fps

    def __add__(self, frames: int) -> Timecode:
        """Return a new :class:`Timecode` advanced by ``frames`` frames."""
        if not isinstance(frames, int):
            return NotImplemented
        return Timecode(frame=self.frame + frames, fps=self.fps)

    def __sub__(self, other: Timecode | int) -> Timecode:
        """Subtract frames (int) or another same-fps :class:`Timecode`.

        ``Timecode - int`` shifts back by that many frames. ``Timecode - Timecode``
        returns the frame difference as a :class:`Timecode` at the same fps;
        mixing frame rates raises :class:`TimecodeError`.
        """
        if isinstance(other, Timecode):
            if other.fps != self.fps:
                raise TimecodeError(
                    f"cannot subtract timecodes at different fps: {self.fps} vs {other.fps}"
                )
            return Timecode(frame=self.frame - other.frame, fps=self.fps)
        if isinstance(other, int):
            return Timecode(frame=self.frame - other, fps=self.fps)
        return NotImplemented


@dataclass(frozen=True)
class TimeRange:
    """A half-open ``[start, end)`` span of timecode at a single frame rate.

    Used for range-anchored notes ("tighten 01:00:05:00-01:00:09:12"). ``start``
    and ``end`` must share ``fps`` and satisfy ``end >= start``; otherwise
    construction raises :class:`TimecodeError`.
    """

    start: Timecode
    end: Timecode

    def __post_init__(self) -> None:
        if self.start.fps != self.end.fps:
            raise TimecodeError(
                f"TimeRange endpoints must share fps: {self.start.fps} vs {self.end.fps}"
            )
        if self.end.frame < self.start.frame:
            raise TimecodeError(
                f"TimeRange end {self.end.to_string()} precedes start {self.start.to_string()}"
            )

    @property
    def duration_frames(self) -> int:
        """Length of the range in frames (``end.frame - start.frame``)."""
        return self.end.frame - self.start.frame

    @property
    def fps(self) -> float:
        """The shared frame rate of this range's endpoints."""
        return self.start.fps

    @classmethod
    def from_strings(cls, start: str, end: str, fps: float) -> TimeRange:
        """Build a :class:`TimeRange` from two ``HH:MM:SS:FF`` strings."""
        return cls(
            start=Timecode.from_string(start, fps),
            end=Timecode.from_string(end, fps),
        )


def parse_timecode(text: str, fps: float) -> Timecode:
    """Module-level convenience wrapper for :meth:`Timecode.from_string`.

    Args:
        text: An ``HH:MM:SS:FF`` timecode string. Surrounding whitespace is
            tolerated; a drop-frame ``;`` separator is rejected.
        fps: The frame rate to interpret the frames field against.

    Returns:
        The parsed :class:`Timecode`.

    Raises:
        TimecodeError: On any malformed or out-of-range input.
    """
    return Timecode.from_string(text, fps)


def format_timecode(frame: int, fps: float) -> str:
    """Render a 0-based ``frame`` index at ``fps`` as ``HH:MM:SS:FF``.

    Inverse of :func:`parse_timecode` composed with ``.frame``. Raises
    :class:`TimecodeError` for a negative frame or non-positive fps.
    """
    if fps <= 0:
        raise TimecodeError(f"fps must be positive, got {fps!r}")
    if frame < 0:
        raise TimecodeError(f"frame index must be non-negative, got {frame}")
    fps_int = _fps_to_int(fps)
    frames = frame % fps_int
    total_seconds = frame // fps_int
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"
