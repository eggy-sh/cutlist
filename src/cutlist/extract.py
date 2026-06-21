"""Extract structured change requests from review prose via a replykit Agent.

The extractor wires a :class:`replykit.ToolRegistry` exposing a single
``emit_change`` tool to a :class:`replykit.Model`. The agent reads the notes and
calls ``emit_change`` once per discrete change it finds; each call's coerced
arguments are captured (via a closure sink) and turned into a
:class:`~cutlist.models.ChangeRequest`. Because replykit's protocol is the
tolerant ``@reply`` text grammar (not strict JSON tool-calling), this works even
with weak/local models, and the whole path is hermetically testable against
:class:`replykit.ScriptedModel` with no network.

Timecode strings the model emits are parsed **deterministically** here against
the caller's ``fps`` — the model never does timecode math, so extraction is
frame-accurate and reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from replykit import Agent, Model, RunResult, ToolRegistry

from .ingest import SourceNote
from .models import Action, ChangeList, ChangeRequest
from .timecode import Timecode, TimecodeError, TimeRange

#: The single tool name the agent calls once per discrete change.
TOOL_NAME = "emit_change"

#: Tool docstring injected into the prompt: teaches the action vocabulary, the
#: timecode format, and the at-vs-range anchoring rule.
_EMIT_DOC = (
    "Record ONE discrete editorial change. Call once per change you find.\n"
    "action: one of cut, trim, extend, insert, replace, reorder, color, audio, "
    "vfx, flag, other.\n"
    "rationale: a short reason in the editor's words.\n"
    "Anchor with EXACTLY ONE of: 'at' for a single point, or 'start'+'end' for a "
    "range. Timecodes are non-drop SMPTE HH:MM:SS:FF (e.g. 01:00:05:00).\n"
    "confidence: 0.0-1.0 certainty. source: provenance label (default 'notes')."
)


@dataclass
class ChangeSink:
    """A capture buffer the ``emit_change`` tool appends parsed requests to.

    The agent's tool calls are side-effecting: each ``emit_change`` invocation
    parses+validates its args (at the caller's ``fps``) into a
    :class:`ChangeRequest` and appends it here. ``errors`` collects per-call
    failures (bad timecode, missing anchor) without aborting the whole run, so
    one malformed note never loses the rest.
    """

    fps: float
    requests: list[ChangeRequest] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def emit(
        self,
        action: str,
        rationale: str,
        at: str = "",
        start: str = "",
        end: str = "",
        confidence: float = 1.0,
        source: str = "notes",
    ) -> str:
        """Tool body: build a :class:`ChangeRequest` and stash it.

        Exactly one anchor must be given: ``at`` (a single ``HH:MM:SS:FF``) or
        the pair ``start``/``end`` (a range). Returns a short human string the
        agent loop echoes back. Records a message in ``errors`` and returns an
        error string instead of raising, so the agent can continue.
        """
        at_str = (at or "").strip()
        start_str = (start or "").strip()
        end_str = (end or "").strip()
        has_at = bool(at_str)
        has_range = bool(start_str) or bool(end_str)

        if has_at and has_range:
            return self._fail(
                "provide either 'at' OR 'start'/'end', not both "
                f"(at={at_str!r}, start={start_str!r}, end={end_str!r})"
            )
        if not has_at and not has_range:
            return self._fail("missing anchor: provide 'at' or 'start'+'end'")
        if has_range and not (start_str and end_str):
            return self._fail("a range needs both 'start' and 'end'")

        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            return self._fail(f"confidence is not a number: {confidence!r}")

        try:
            anchor_at: Timecode | None = None
            span: TimeRange | None = None
            if has_at:
                anchor_at = Timecode.from_string(at_str, self.fps)
            else:
                span = TimeRange.from_strings(start_str, end_str, self.fps)
            request = ChangeRequest(
                action=Action.coerce(action),
                rationale=(rationale or "").strip(),
                at=anchor_at,
                span=span,
                confidence=conf,
                source=(source or "notes").strip() or "notes",
            )
        except (TimecodeError, ValueError) as exc:
            return self._fail(str(exc))

        self.requests.append(request)
        anchor_desc = at_str if has_at else f"{start_str}-{end_str}"
        return f"recorded {request.action.value} @ {anchor_desc}"

    def _fail(self, message: str) -> str:
        self.errors.append(message)
        return f"ERROR: {message}"


def build_registry(sink: ChangeSink) -> ToolRegistry:
    """Build the one-tool :class:`replykit.ToolRegistry` for extraction.

    Registers ``sink.emit`` under the tool name ``emit_change`` with a docstring
    that teaches the model the action vocabulary, the timecode format, and the
    at-vs-range anchoring rule. Injected once into the agent prompt via the
    registry's ``describe()``.
    """
    registry = ToolRegistry()
    registry.register(sink.emit, name=TOOL_NAME, description=_EMIT_DOC)
    return registry


def build_prompt(notes: list[SourceNote], fps: float) -> str:
    """Render the source notes (+ any timecode hints) into the agent task string.

    Deterministic given its inputs: fragments are concatenated in order with
    their provenance and any out-of-band Frame.io timecode hint, plus the target
    ``fps`` so the model emits frames in the right base.
    """
    lines = [
        "Read the following review notes and emit one change per discrete request.",
        f"Target frame rate: {fps} fps (non-drop). Emit timecodes as HH:MM:SS:FF.",
        "",
        "Notes:",
    ]
    for index, note in enumerate(notes, start=1):
        hint = f" [timecode hint: {note.timecode_hint}]" if note.timecode_hint else ""
        lines.append(f"{index}. ({note.source}){hint} {note.text}")
    return "\n".join(lines)


def extract_changes(
    notes: list[SourceNote],
    model: Model,
    fps: float,
    *,
    title: str = "cutlist",
    max_steps: int = 16,
    on_run: Callable[[RunResult], None] | None = None,
) -> ChangeList:
    """Run the extraction agent and return a sorted :class:`ChangeList`.

    Args:
        notes: Source fragments from :mod:`cutlist.ingest`.
        model: Any :class:`replykit.Model` (real adapter or a test double).
        fps: Frame rate every emitted timecode is parsed against.
        title: Title stamped onto the resulting :class:`ChangeList`.
        max_steps: Hard cap on agent steps (passed to :class:`replykit.Agent`).
        on_run: Optional callback handed the raw :class:`replykit.RunResult`
            (used by the CLI to surface token/cost telemetry).

    Returns:
        A :class:`ChangeList` of the captured requests, sorted by start frame.
        Per-note extraction errors are dropped from the result but recorded on
        the sink and reflected via ``on_run`` for the caller to report.
    """
    sink = ChangeSink(fps=fps)
    registry = build_registry(sink)
    agent = Agent(model, registry, max_steps=max_steps)
    prompt = build_prompt(notes, fps)
    run_result = agent.run(prompt)
    if on_run is not None:
        on_run(run_result)
    change_list = ChangeList.from_requests(sink.requests, fps, title=title)
    return change_list.sorted()
