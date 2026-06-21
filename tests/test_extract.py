"""Unit tests for the replykit-powered extractor (hermetic, ScriptedModel)."""

from __future__ import annotations

from pathlib import Path

from replykit import RunResult, ScriptedModel

from cutlist.extract import (
    TOOL_NAME,
    ChangeSink,
    build_prompt,
    build_registry,
    extract_changes,
)
from cutlist.ingest import SourceNote, load_notes
from cutlist.models import Action


def test_extract_changes_matches_scripted_emissions(
    notes_file: Path,
    scripted_model: ScriptedModel,
    fps: float,
) -> None:
    cl = extract_changes(load_notes(notes_file), scripted_model, fps)
    assert len(cl) == 3
    assert cl.fps == fps
    # Sorted by start frame ascending.
    frames = [r.start_frame for r in cl]
    assert frames == sorted(frames)
    # The three scripted emissions in start-frame order:
    # 01:00:05:00 (point), 01:00:12:00-19:12 (span), 01:00:30:00 (point).
    first, second, third = list(cl)
    assert first.at is not None and first.at.to_string() == "01:00:05:00"
    assert first.action is Action.TRIM
    assert first.confidence == 0.9
    assert first.source == "notes"

    assert second.span is not None
    assert second.span.start.to_string() == "01:00:12:00"
    assert second.span.end.to_string() == "01:00:19:12"
    assert second.action is Action.TRIM
    assert second.confidence == 0.95

    assert third.at is not None and third.at.to_string() == "01:00:30:00"
    assert third.action is Action.FLAG
    assert third.confidence == 0.8


def test_on_run_receives_telemetry(
    notes_file: Path,
    scripted_model: ScriptedModel,
    fps: float,
) -> None:
    captured: list[RunResult] = []
    extract_changes(load_notes(notes_file), scripted_model, fps, on_run=captured.append)
    assert len(captured) == 1
    run = captured[0]
    assert isinstance(run, RunResult)
    telemetry = run.telemetry.as_dict()
    assert telemetry["calls"] >= 1
    # Non-empty token accounting.
    assert telemetry["total_input_tokens"] > 0
    assert telemetry["total_output_tokens"] > 0


def test_extract_preserves_title(
    notes_file: Path,
    scripted_model: ScriptedModel,
    fps: float,
) -> None:
    cl = extract_changes(load_notes(notes_file), scripted_model, fps, title="director review")
    assert cl.title == "director review"


def test_sink_emit_point_request(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    out = sink.emit(action="cut", rationale="remove this", at="01:00:05:00")
    assert "recorded" in out
    assert len(sink.requests) == 1
    assert not sink.errors
    assert sink.requests[0].at is not None


def test_sink_emit_range_request(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    out = sink.emit(action="trim", rationale="tighten", start="01:00:05:00", end="01:00:09:00")
    assert "recorded" in out
    assert sink.requests[0].span is not None


def test_sink_emit_bad_timecode_records_error_not_raise(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(action="cut", rationale="bad tc", at="99:99:99:99")
    assert result.startswith("ERROR")
    assert sink.errors
    assert not sink.requests


def test_sink_emit_drop_frame_records_error(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(action="cut", rationale="df", at="01:00:00;00")
    assert result.startswith("ERROR")
    assert sink.errors


def test_sink_emit_both_anchors_is_error(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(
        action="cut", rationale="x", at="01:00:05:00", start="01:00:05:00", end="01:00:09:00"
    )
    assert result.startswith("ERROR")
    assert not sink.requests


def test_sink_emit_no_anchor_is_error(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(action="cut", rationale="x")
    assert result.startswith("ERROR")
    assert not sink.requests


def test_sink_emit_partial_range_is_error(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(action="trim", rationale="x", start="01:00:05:00")
    assert result.startswith("ERROR")
    assert not sink.requests


def test_sink_emit_bad_confidence_is_error(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    result = sink.emit(action="cut", rationale="x", at="01:00:05:00", confidence="notnum")
    assert result.startswith("ERROR")
    assert not sink.requests
    # And out-of-range confidence surfaces via ChangeRequest validation.
    sink2 = ChangeSink(fps=fps)
    result2 = sink2.emit(action="cut", rationale="x", at="01:00:05:00", confidence=2.0)
    assert result2.startswith("ERROR")
    assert not sink2.requests


def test_one_bad_note_does_not_lose_the_rest(fps: float) -> None:
    notes = [SourceNote("a", "notes"), SourceNote("b", "notes")]
    emissions = [
        "@reply name=emit_change\naction = cut\nrationale = good\nat = 01:00:05:00\n@end",
        "@reply name=emit_change\naction = cut\nrationale = bad\nat = 99:99:99:99\n@end",
        "@reply name=emit_change\naction = flag\nrationale = good2\nat = 01:00:30:00\n@end",
        "Done.",
    ]
    model = ScriptedModel(emissions)
    runs: list[RunResult] = []
    cl = extract_changes(notes, model, fps, on_run=runs.append)
    # Two good requests survive; the bad one is dropped.
    assert len(cl) == 2
    assert {r.rationale for r in cl} == {"good", "good2"}


def test_build_registry_exposes_one_tool(fps: float) -> None:
    sink = ChangeSink(fps=fps)
    registry = build_registry(sink)
    assert len(registry) == 1
    assert TOOL_NAME in registry
    described = registry.describe()
    assert "HH:MM:SS:FF" in described
    lowered = described.lower()
    for verb in ("cut", "trim", "extend", "flag"):
        assert verb in lowered


def test_build_prompt_is_deterministic_and_has_provenance(fps: float) -> None:
    notes = [
        SourceNote("first note", "notes"),
        SourceNote("from frameio", "c-101", timecode_hint="01:00:05:00"),
    ]
    p1 = build_prompt(notes, fps)
    p2 = build_prompt(notes, fps)
    assert p1 == p2
    assert "first note" in p1
    assert "c-101" in p1
    assert "01:00:05:00" in p1
    assert "24" in p1
