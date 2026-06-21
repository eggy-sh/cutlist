"""The structured records cutlist extracts and exports.

A :class:`ChangeRequest` is one discrete, timecode-anchored editorial change
parsed out of review prose. A :class:`ChangeList` is an ordered, fps-tagged
collection of them — the single payload both exporters consume. Both are plain
frozen dataclasses with ``to_dict`` / ``from_dict`` round-trips so the CLI's
``--json`` output is a faithful, reload-able mirror of the in-memory objects.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .timecode import Timecode, TimeRange


class Action(StrEnum):
    """The controlled vocabulary of editorial actions a note can request.

    Free-form verbs from the prose ("chop", "lose", "punch in") are normalized by
    the extractor onto this closed set so downstream tooling can switch on a known
    enum rather than arbitrary strings. ``OTHER`` is the catch-all.
    """

    CUT = "cut"
    TRIM = "trim"
    EXTEND = "extend"
    INSERT = "insert"
    REPLACE = "replace"
    REORDER = "reorder"
    COLOR = "color"
    AUDIO = "audio"
    VFX = "vfx"
    FLAG = "flag"
    OTHER = "other"

    @classmethod
    def coerce(cls, value: str) -> Action:
        """Map a raw verb/string onto a member, defaulting to :attr:`OTHER`.

        Accepts member values ("cut"), names ("CUT"), and a small synonym table
        (e.g. "delete"/"remove" -> CUT, "tighten"/"shorten" -> TRIM). Never
        raises; unknown verbs become :attr:`OTHER`.
        """
        if isinstance(value, Action):
            return value
        if value is None:
            return cls.OTHER
        raw = str(value).strip()
        if not raw:
            return cls.OTHER
        key = raw.lower()
        # Member value ("cut") or NAME ("CUT") match. Each member's NAME is the
        # uppercase of its value, so a single case-insensitive lookup covers both
        # the value form and the name form named in the public contract.
        if key in _BY_VALUE:
            return _BY_VALUE[key]
        # Synonym table (e.g. "delete" -> CUT, "tighten" -> TRIM).
        return _SYNONYMS.get(key, cls.OTHER)


#: Lower-cased member value (== lower-cased member NAME) -> member, for O(1)
#: coercion lookup covering both the "cut" and "CUT" spellings.
_BY_VALUE: dict[str, Action] = {member.value: member for member in Action}

#: Free-form verbs mapped onto the closed :class:`Action` vocabulary. Lower-cased.
_SYNONYMS: dict[str, Action] = {
    "delete": Action.CUT,
    "remove": Action.CUT,
    "chop": Action.CUT,
    "drop": Action.CUT,
    "lose": Action.CUT,
    "kill": Action.CUT,
    "tighten": Action.TRIM,
    "shorten": Action.TRIM,
    "trim down": Action.TRIM,
    "punch in": Action.TRIM,
    "lengthen": Action.EXTEND,
    "hold": Action.EXTEND,
    "extend": Action.EXTEND,
    "add": Action.INSERT,
    "insert": Action.INSERT,
    "swap": Action.REPLACE,
    "replace": Action.REPLACE,
    "rearrange": Action.REORDER,
    "move": Action.REORDER,
    "reorder": Action.REORDER,
    "grade": Action.COLOR,
    "colour": Action.COLOR,
    "warm": Action.COLOR,
    "cool": Action.COLOR,
    "sound": Action.AUDIO,
    "mix": Action.AUDIO,
    "music": Action.AUDIO,
    "effect": Action.VFX,
    "effects": Action.VFX,
    "comp": Action.VFX,
    "note": Action.FLAG,
    "flag": Action.FLAG,
    "fix": Action.FLAG,
    "check": Action.FLAG,
}


@dataclass(frozen=True)
class ChangeRequest:
    """One timecode-anchored editorial change request.

    Exactly one of ``at`` / ``span`` is populated: ``at`` for a point note
    ("at 01:00:05:00 add a title"), ``span`` for a range note ("trim
    01:00:05:00-01:00:09:00"). ``confidence`` is the extractor's 0.0-1.0
    self-rated certainty. ``source`` records provenance (e.g. "notes" or a
    Frame.io comment id) for auditability.
    """

    action: Action
    rationale: str
    at: Timecode | None = None
    span: TimeRange | None = None
    confidence: float = 1.0
    source: str = "notes"

    def __post_init__(self) -> None:
        """Validate the at/span XOR invariant and the confidence range.

        Raises:
            ValueError: If neither or both of ``at``/``span`` are set, or if
                ``confidence`` is outside ``[0.0, 1.0]``.
        """
        has_at = self.at is not None
        has_span = self.span is not None
        if has_at == has_span:
            raise ValueError("ChangeRequest requires exactly one of 'at' (point) or 'span' (range)")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {self.confidence!r}")

    @property
    def fps(self) -> float:
        """The frame rate of this request's anchor (from ``at`` or ``span``)."""
        if self.at is not None:
            return self.at.fps
        assert self.span is not None  # XOR invariant guarantees this
        return self.span.fps

    @property
    def start_frame(self) -> int:
        """The start frame of this request's anchor (point frame or span start)."""
        if self.at is not None:
            return self.at.frame
        assert self.span is not None
        return self.span.start.frame

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable view: timecodes as strings, action as its value."""
        data: dict[str, Any] = {
            "action": self.action.value,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.at is not None:
            data["at"] = self.at.to_string()
        if self.span is not None:
            data["start"] = self.span.start.to_string()
            data["end"] = self.span.end.to_string()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], fps: float) -> ChangeRequest:
        """Rebuild a :class:`ChangeRequest` from :meth:`to_dict` output at ``fps``."""
        at: Timecode | None = None
        span: TimeRange | None = None
        if data.get("at") is not None:
            at = Timecode.from_string(data["at"], fps)
        if data.get("start") is not None and data.get("end") is not None:
            span = TimeRange.from_strings(data["start"], data["end"], fps)
        return cls(
            action=Action.coerce(data["action"]),
            rationale=data.get("rationale", ""),
            at=at,
            span=span,
            confidence=float(data.get("confidence", 1.0)),
            source=data.get("source", "notes"),
        )


@dataclass(frozen=True)
class ChangeList:
    """An ordered, fps-tagged collection of :class:`ChangeRequest` records.

    This is the single object both exporters (:func:`cutlist.to_otio`,
    :func:`cutlist.to_edl`) consume. All contained requests must share the
    list's ``fps``; :meth:`from_requests` enforces that.
    """

    fps: float
    requests: tuple[ChangeRequest, ...] = ()
    title: str = "cutlist"

    def __len__(self) -> int:
        return len(self.requests)

    def __iter__(self) -> Iterator[ChangeRequest]:
        return iter(self.requests)

    @classmethod
    def from_requests(
        cls,
        requests: list[ChangeRequest],
        fps: float,
        *,
        title: str = "cutlist",
    ) -> ChangeList:
        """Build a list, asserting every request's fps matches ``fps``.

        Raises:
            ValueError: If any request's anchor fps differs from ``fps``.
        """
        materialized = tuple(requests)
        for req in materialized:
            if req.fps != fps:
                raise ValueError(f"request fps {req.fps} does not match list fps {fps}")
        return cls(fps=fps, requests=materialized, title=title)

    def sorted(self) -> ChangeList:
        """Return a copy with requests ordered by their start frame, ascending."""
        ordered = tuple(sorted(self.requests, key=lambda r: r.start_frame))
        return ChangeList(fps=self.fps, requests=ordered, title=self.title)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the whole list (fps, title, requests)."""
        return {
            "title": self.title,
            "fps": self.fps,
            "requests": [r.to_dict() for r in self.requests],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChangeList:
        """Rebuild a :class:`ChangeList` from :meth:`to_dict` output."""
        fps = float(data["fps"])
        requests = tuple(ChangeRequest.from_dict(item, fps) for item in data.get("requests", []))
        return cls(fps=fps, requests=requests, title=data.get("title", "cutlist"))
