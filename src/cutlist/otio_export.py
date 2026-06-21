"""Export a :class:`~cutlist.models.ChangeList` to OpenTimelineIO.

OpenTimelineIO (OTIO) is the studio-standard interchange. This module builds an
``otio.schema.Timeline`` where each :class:`~cutlist.models.ChangeRequest` is
represented as a ``Marker`` (point notes) or a flagged ``Clip`` (range notes) on
a single video track, with the action, rationale, confidence, and source carried
in the object's ``metadata["cutlist"]`` namespace and its ``name``. Timing uses
``otio.opentime.RationalTime`` / ``TimeRange`` at the list's ``fps``, so the
result is frame-accurate.

The output is the product: it must **round-trip** through OTIO
(``otio.adapters.read_from_string(otio.adapters.write_to_string(tl)) == tl``),
which the test suite asserts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio

from .models import ChangeList, ChangeRequest

#: The metadata namespace key under which cutlist stamps its fields onto OTIO
#: objects, so a reader can recover the structured change request.
METADATA_NAMESPACE = "cutlist"


def _metadata(change: ChangeRequest) -> dict[str, Any]:
    """The cutlist fields stamped under ``metadata[METADATA_NAMESPACE]``."""
    return {
        "action": change.action.value,
        "rationale": change.rationale,
        "confidence": change.confidence,
        "source": change.source,
    }


def _name(change: ChangeRequest, index: int) -> str:
    """A stable, human-readable object name for a change."""
    return f"{index:03d}_{change.action.value}"


def build_timeline(changes: ChangeList) -> Any:
    """Build (but do not serialize) an ``otio.schema.Timeline`` for ``changes``.

    Returns the live OTIO object so callers can inspect or further mutate it.
    Each request becomes a marker (point) or flagged clip (span) on one video
    track named after ``changes.title``; cutlist fields land under
    ``metadata[METADATA_NAMESPACE]``. The return type is ``Any`` to keep the
    public signature import-light, but it is always an ``otio.schema.Timeline``.
    """
    fps = changes.fps
    timeline = otio.schema.Timeline(name=changes.title)
    track = otio.schema.Track(
        name=changes.title,
        kind=otio.schema.TrackKind.Video,
    )
    timeline.tracks.append(track)

    ordered = changes.sorted()
    for index, change in enumerate(ordered.requests, start=1):
        name = _name(change, index)
        meta = {METADATA_NAMESPACE: _metadata(change)}
        if change.at is not None:
            # Point note -> zero-duration marker on the track.
            marker = otio.schema.Marker(
                name=name,
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(change.at.frame, fps),
                    duration=otio.opentime.RationalTime(0, fps),
                ),
                metadata=meta,
            )
            track.markers.append(marker)
        else:
            # Range note -> a clip spanning the range.
            span = change.span
            assert span is not None  # XOR invariant
            clip = otio.schema.Clip(
                name=name,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(span.start.frame, fps),
                    duration=otio.opentime.RationalTime(span.duration_frames, fps),
                ),
                metadata=meta,
            )
            track.append(clip)
    return timeline


def to_otio(changes: ChangeList, path: str | Path) -> Path:
    """Write ``changes`` to an ``.otio`` file and return the written path.

    Builds the timeline via :func:`build_timeline` and serializes it with
    ``otio.adapters.write_to_file`` (the native JSON ``otio_json`` adapter).

    Raises:
        ValueError: If ``changes`` is empty (an empty timeline is not a useful
            deliverable; the caller should be told rather than write a no-op).
    """
    if len(changes) == 0:
        raise ValueError("cannot export an empty ChangeList to OTIO")
    timeline = build_timeline(changes)
    out = Path(path)
    otio.adapters.write_to_file(timeline, str(out), "otio_json")
    return out


def to_otio_string(changes: ChangeList) -> str:
    """Serialize ``changes`` to an OTIO JSON string (in-memory, for tests/CLI)."""
    if len(changes) == 0:
        raise ValueError("cannot export an empty ChangeList to OTIO")
    timeline = build_timeline(changes)
    return otio.adapters.write_to_string(timeline, "otio_json")
