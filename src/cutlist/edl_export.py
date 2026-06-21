"""Export a :class:`~cutlist.models.ChangeList` to a CMX3600 EDL.

CMX3600 is the lingua-franca edit decision list that virtually every NLE imports.
This module emits a deterministic, spec-shaped ``.edl``: a ``TITLE:`` header, an
``FCM: NON-DROP FRAME`` line, then one numbered event per change request. Each
event carries standard CMX columns (event number, reel, channel, transition,
source in/out, record in/out) plus the change's rationale as a ``* `` comment
line and an action/confidence annotation so the structured fields survive.

Correctness is the product: the emitted text must parse cleanly via OTIO's
``cmx_3600`` adapter (``otio.adapters.read_from_string(edl, "cmx_3600")``), which
the test suite asserts in addition to byte-level golden checks.
"""

from __future__ import annotations

from pathlib import Path

from .models import ChangeList, ChangeRequest
from .timecode import format_timecode

#: Default reel name used when a change has no associated source reel.
DEFAULT_REEL = "AX"

#: Channel / track designator (video).
_CHANNEL = "V"

#: Transition code: a straight cut.
_TRANSITION = "C"


def _format_event(
    index: int,
    change: ChangeRequest,
    fps: float,
) -> str:
    """Render a single numbered CMX event block for ``change`` (internal helper).

    Exposed for unit testing of the per-event formatting in isolation. Returns
    the event line plus its comment lines, each terminated by a newline.
    """
    if change.at is not None:
        # Point note -> a 1-frame event.
        src_in = change.at.frame
        src_out = change.at.frame + 1
    else:
        span = change.span
        assert span is not None  # XOR invariant
        src_in = span.start.frame
        src_out = span.end.frame
        if src_out == src_in:
            # Degenerate zero-length range -> still emit a visible 1-frame event.
            src_out = src_in + 1

    # Record timecodes mirror the source timecodes (assemble in place).
    rec_in, rec_out = src_in, src_out

    src_in_tc = format_timecode(src_in, fps)
    src_out_tc = format_timecode(src_out, fps)
    rec_in_tc = format_timecode(rec_in, fps)
    rec_out_tc = format_timecode(rec_out, fps)

    event_line = (
        f"{index:03d}  {DEFAULT_REEL:<8}{_CHANNEL:<6}{_TRANSITION:<9}"
        f"{src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}"
    )
    lines = [event_line]
    lines.append(f"* {change.action.value.upper()} (confidence {change.confidence:.2f})")
    rationale = change.rationale.strip()
    if rationale:
        lines.append(f"* {rationale}")
    lines.append(f"* SOURCE: {change.source}")
    return "\n".join(lines) + "\n"


def build_edl(changes: ChangeList) -> str:
    """Render ``changes`` to CMX3600 EDL text (the full file body, with header).

    Deterministic for a given :class:`ChangeList`: events are numbered from 001
    in the list's sorted order, timecodes are formatted at ``changes.fps``, and
    point notes are emitted as 1-frame-duration events. The returned string ends
    with a trailing newline.

    Raises:
        ValueError: If ``changes`` is empty.
    """
    if len(changes) == 0:
        raise ValueError("cannot export an empty ChangeList to EDL")
    fps = changes.fps
    ordered = changes.sorted()
    parts = [f"TITLE: {changes.title}", "FCM: NON-DROP FRAME", ""]
    body = "\n".join(parts) + "\n"
    blocks = [
        _format_event(index, change, fps) for index, change in enumerate(ordered.requests, start=1)
    ]
    return body + "\n".join(blocks)


def to_edl(changes: ChangeList, path: str | Path) -> Path:
    """Write ``changes`` to a ``.edl`` file (via :func:`build_edl`) and return the path.

    Raises:
        ValueError: If ``changes`` is empty.
    """
    text = build_edl(changes)
    out = Path(path)
    out.write_text(text, encoding="utf-8")
    return out
