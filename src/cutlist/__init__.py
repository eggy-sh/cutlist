"""cutlist — an editor's-notes router built on the replykit engine.

cutlist ingests free-form director/editor review prose (and, optionally, a
Frame.io comment CSV) and uses a :class:`replykit.Agent` to extract discrete,
timecode-anchored **change requests** as structured :class:`ChangeRequest`
records (action verb, target timecode or range, rationale, confidence). The
change list is then exported to two studio-standard interchange formats:

* **OpenTimelineIO** (``.otio``) — a timeline whose markers/clips carry the
  change requests, round-trippable through ``opentimelineio``.
* **CMX3600 EDL** (``.edl``) — a plain-text edit decision list that parses
  cleanly in NLEs and in ``opentimelineio``'s ``cmx_3600`` adapter.

Timecode parsing is deterministic and frame-rate aware (``HH:MM:SS:FF``), so the
same prose always yields the same frame-accurate output.

This top-level module is the stable public import surface for v0.1. Submodules:

* :mod:`cutlist.timecode` — frame-rate-aware timecode parsing/formatting.
* :mod:`cutlist.models` — the :class:`ChangeRequest` / :class:`ChangeList` records.
* :mod:`cutlist.ingest` — read notes prose + Frame.io CSV into source text.
* :mod:`cutlist.extract` — the replykit Agent that extracts change requests.
* :mod:`cutlist.otio_export` — change list -> OpenTimelineIO timeline.
* :mod:`cutlist.edl_export` — change list -> CMX3600 EDL.
"""

from __future__ import annotations

from .edl_export import to_edl
from .extract import build_registry, extract_changes
from .ingest import load_frameio_csv, load_notes
from .models import Action, ChangeList, ChangeRequest
from .otio_export import to_otio
from .timecode import (
    Timecode,
    TimecodeError,
    TimeRange,
    format_timecode,
    parse_timecode,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # timecode
    "Timecode",
    "TimeRange",
    "TimecodeError",
    "parse_timecode",
    "format_timecode",
    # models
    "Action",
    "ChangeRequest",
    "ChangeList",
    # ingest
    "load_notes",
    "load_frameio_csv",
    # extract
    "extract_changes",
    "build_registry",
    # export
    "to_otio",
    "to_edl",
]
