"""Integration tests for the ``cutlist`` Typer CLI.

These exercise the CLI as a user / automation pipeline would: through Typer's
:class:`CliRunner`, asserting exit codes, the human-mode Rich tables, and the
``--json`` automation contract (exactly one JSON object on stdout, nothing else).

Everything is **hermetic**: no network, no live LLM. Extraction runs against
either the CLI's built-in offline :class:`~cutlist.cli.HeuristicModel` (the
default) or, where a scripted-LLM path is wanted, the ``scripted_model``
conftest fixture (a :class:`replykit.ScriptedModel`) injected via monkeypatch.
Exporter correctness is verified by reading the written deliverables back through
OpenTimelineIO (OTIO round-trip + ``cmx_3600`` EDL parse).
"""

from __future__ import annotations

import json
from pathlib import Path

import opentimelineio as otio
import pytest
from typer.testing import CliRunner

from cutlist import cli
from cutlist.cli import HeuristicModel, app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _one_json_object(stdout: str) -> dict:
    """Assert stdout is exactly one JSON object (the automation contract)."""
    nonblank = [line for line in stdout.splitlines() if line.strip()]
    assert len(nonblank) == 1, f"expected exactly one stdout line, got {nonblank!r}"
    obj = json.loads(stdout)
    assert isinstance(obj, dict)
    return obj


# ---------------------------------------------------------------------------
# Top-level wiring
# ---------------------------------------------------------------------------


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # no_args_is_help => exit 0 (or 2 on some Click versions) and usage text.
    assert "parse" in result.stdout
    assert "export" in result.stdout


def test_parse_help_documents_flags() -> None:
    result = runner.invoke(app, ["parse", "--help"])
    assert result.exit_code == 0
    for flag in ("--fps", "--frameio", "--json", "--model"):
        assert flag in result.stdout


def test_export_help_documents_flags() -> None:
    result = runner.invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    for flag in ("--format", "--fps", "--output", "--frameio", "--json"):
        assert flag in result.stdout


# ---------------------------------------------------------------------------
# The offline HeuristicModel (default backend)
# ---------------------------------------------------------------------------


def test_heuristic_model_is_a_replykit_model() -> None:
    from replykit import Model

    assert isinstance(HeuristicModel(), Model)


def test_heuristic_emits_point_and_range_blocks() -> None:
    prompt = (
        "Notes:\n"
        "1. (notes) At 01:00:05:00 punch in on the speaker.\n"
        "2. (notes) Tighten 01:00:12:00-01:00:19:12 the pause drags.\n"
    )
    out = HeuristicModel().complete(prompt).text
    assert "@reply name=emit_change" in out
    assert "at = 01:00:05:00" in out
    assert "start = 01:00:12:00" in out
    assert "end = 01:00:19:12" in out


def test_heuristic_ignores_example_timecode_in_tool_description() -> None:
    # The injected tool description teaches the format with an example TC; the
    # stand-in must not mistake it for a real note (it scopes to the Notes: body).
    prompt = (
        "Timecodes are HH:MM:SS:FF (e.g. 01:00:05:00).\n"
        "@reply name=emit_change\n@end\n\n"
        "Notes:\n"
        "1. (notes) At 02:00:00:00 do the thing.\n"
    )
    out = HeuristicModel().complete(prompt).text
    assert "at = 02:00:00:00" in out
    assert "01:00:05:00" not in out


def test_heuristic_second_turn_is_final_answer() -> None:
    # Once the agent feeds tool results back, the model must end the loop with a
    # plain-text (no @reply) turn.
    out = HeuristicModel().complete("anything\nTool results:\n- emit_change: ok").text
    assert "@reply" not in out


# ---------------------------------------------------------------------------
# parse — human mode
# ---------------------------------------------------------------------------


def test_parse_human_renders_table_and_telemetry(notes_file: Path) -> None:
    result = runner.invoke(app, ["parse", str(notes_file)])
    assert result.exit_code == 0
    # Table columns + telemetry block.
    assert "action" in result.stdout
    assert "rationale" in result.stdout
    assert "Telemetry" in result.stdout
    # The three timecode anchors from SAMPLE_NOTES survive into the output.
    assert "01:00:05:00" in result.stdout
    assert "01:00:30:00" in result.stdout


def test_parse_human_no_timecodes_says_none(tmp_path: Path) -> None:
    p = tmp_path / "vibes.txt"
    p.write_text("Make it feel warmer overall, no timecodes here.\n", encoding="utf-8")
    result = runner.invoke(app, ["parse", str(p)])
    assert result.exit_code == 0
    assert "No change requests" in result.stdout


# ---------------------------------------------------------------------------
# parse — JSON mode (automation contract)
# ---------------------------------------------------------------------------


def test_parse_json_is_single_object_and_round_trips(notes_file: Path) -> None:
    result = runner.invoke(app, ["parse", str(notes_file), "--json"])
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)

    # ChangeList.to_dict shape + CLI telemetry/errors envelope.
    assert obj["fps"] == 24.0
    assert obj["title"] == "notes"
    assert isinstance(obj["requests"], list)
    assert len(obj["requests"]) == 3
    assert "telemetry" in obj
    assert obj["telemetry"]["calls"] >= 1
    assert "errors" in obj

    # The payload is a faithful, reload-able ChangeList mirror.
    from cutlist.models import ChangeList

    reloaded = ChangeList.from_dict(obj)
    assert len(reloaded) == 3
    assert reloaded.fps == 24.0


def test_parse_json_requests_sorted_by_start_frame(notes_file: Path) -> None:
    result = runner.invoke(app, ["parse", str(notes_file), "--json"])
    obj = _one_json_object(result.stdout)
    # ChangeRequest.to_dict emits a point note as a flat "at" and a range note as
    # flat "start"/"end" keys; the start anchor is what the list is sorted on.
    anchors = [req.get("at") or req.get("start") for req in obj["requests"]]
    assert all(a is not None for a in anchors)
    assert anchors == sorted(anchors)


def test_parse_json_custom_fps_changes_frame_base(notes_file: Path) -> None:
    # At 30fps the same HH:MM:SS:FF parses to a different frame count; the JSON
    # mirror must carry the chosen fps so it reloads correctly.
    result = runner.invoke(app, ["parse", str(notes_file), "--fps", "30", "--json"])
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert obj["fps"] == 30.0


# ---------------------------------------------------------------------------
# --frameio folds in the comment CSV
# ---------------------------------------------------------------------------


def test_parse_frameio_folds_in_comments(notes_file: Path, frameio_csv_file: Path) -> None:
    base = runner.invoke(app, ["parse", str(notes_file), "--json"])
    base_obj = _one_json_object(base.stdout)

    merged = runner.invoke(
        app, ["parse", str(notes_file), "--frameio", str(frameio_csv_file), "--json"]
    )
    assert merged.exit_code == 0
    merged_obj = _one_json_object(merged.stdout)

    # The CSV adds at least one extra anchored note (01:00:40:00 in the sample).
    assert len(merged_obj["requests"]) > len(base_obj["requests"])
    anchors = {r.get("at") for r in merged_obj["requests"]}
    assert "01:00:40:00" in anchors


def test_parse_missing_frameio_csv_errors_cleanly(notes_file: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.csv"
    result = runner.invoke(app, ["parse", str(notes_file), "--frameio", str(missing), "--json"])
    assert result.exit_code == 1
    obj = _one_json_object(result.stdout)
    assert "error" in obj
    assert "Frame.io" in obj["error"]


# ---------------------------------------------------------------------------
# export — writes deliverables
# ---------------------------------------------------------------------------


def test_export_otio_writes_roundtrippable_file(notes_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "review"
    result = runner.invoke(
        app, ["export", str(notes_file), "--format", "otio", "-o", str(out), "--json"]
    )
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert obj["format"] == "otio"
    written = [Path(p) for p in obj["written"]]
    assert len(written) == 1
    otio_path = written[0]
    assert otio_path.suffix == ".otio"
    assert otio_path.exists()

    # Correctness: it reads back through OTIO and the serialize round-trip is stable.
    tl = otio.adapters.read_from_file(str(otio_path))
    assert len(tl.tracks) == 1
    s1 = otio.adapters.write_to_string(tl, "otio_json")
    tl2 = otio.adapters.read_from_string(s1, "otio_json")
    s2 = otio.adapters.write_to_string(tl2, "otio_json")
    assert s1 == s2


def test_export_edl_parses_via_cmx3600(notes_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "review"
    result = runner.invoke(
        app, ["export", str(notes_file), "--format", "edl", "-o", str(out), "--json"]
    )
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert obj["format"] == "edl"
    edl_path = Path(obj["written"][0])
    assert edl_path.suffix == ".edl"

    body = edl_path.read_text()
    assert body.startswith("TITLE:")
    assert "FCM: NON-DROP FRAME" in body

    # Correctness: the EDL parses cleanly via OTIO's cmx_3600 adapter.
    timeline = otio.adapters.read_from_file(str(edl_path), "cmx_3600")
    assert timeline is not None


def test_export_both_writes_two_files(notes_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "review"
    result = runner.invoke(
        app, ["export", str(notes_file), "--format", "both", "-o", str(out), "--json"]
    )
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert obj["format"] == "both"
    written = sorted(Path(p).suffix for p in obj["written"])
    assert written == [".edl", ".otio"]
    for p in obj["written"]:
        assert Path(p).exists()
    assert obj["changes"] == 3


def test_export_human_mode_lists_paths(notes_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "review"
    result = runner.invoke(app, ["export", str(notes_file), "--format", "both", "-o", str(out)])
    assert result.exit_code == 0
    assert "Wrote" in result.stdout
    assert "review.otio" in result.stdout
    assert "review.edl" in result.stdout


def test_export_bad_format_errors(notes_file: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["export", str(notes_file), "--format", "xml", "-o", str(tmp_path / "x"), "--json"],
    )
    assert result.exit_code == 1
    obj = _one_json_object(result.stdout)
    assert "error" in obj
    assert "format" in obj["error"]


def test_export_empty_changelist_errors(tmp_path: Path) -> None:
    p = tmp_path / "empty.txt"
    p.write_text("No timecodes anywhere, just feelings.\n", encoding="utf-8")
    result = runner.invoke(
        app, ["export", str(p), "--format", "otio", "-o", str(tmp_path / "e"), "--json"]
    )
    assert result.exit_code == 1
    obj = _one_json_object(result.stdout)
    assert "error" in obj


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_parse_missing_notes_file_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["parse", str(tmp_path / "nope.txt"), "--json"])
    assert result.exit_code == 1
    obj = _one_json_object(result.stdout)
    assert "not found" in obj["error"]


def test_parse_missing_notes_file_human(tmp_path: Path) -> None:
    result = runner.invoke(app, ["parse", str(tmp_path / "nope.txt")])
    assert result.exit_code == 1
    # Error goes to stderr, never polluting stdout.
    assert result.stdout.strip() == ""
    assert "not found" in result.stderr


def test_unknown_model_errors_cleanly(notes_file: Path) -> None:
    result = runner.invoke(app, ["parse", str(notes_file), "--model", "telepathy", "--json"])
    assert result.exit_code == 1
    obj = _one_json_object(result.stdout)
    assert "unknown --model" in obj["error"]


# ---------------------------------------------------------------------------
# A scripted-LLM extraction path (proves the CLI is provider-agnostic and that a
# real model's emissions flow through unchanged). We monkeypatch the CLI's model
# builder to return the conftest ScriptedModel instead of the offline default.
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_scripted_model(monkeypatch: pytest.MonkeyPatch, scripted_model) -> None:
    monkeypatch.setattr(cli, "_build_model", lambda backend, model_name: scripted_model)


def test_parse_with_scripted_model(notes_file: Path, _patch_scripted_model: None) -> None:
    # scripted_emissions emits 3 changes then a final answer; the CLI must surface
    # exactly those, regardless of which "backend" name is passed.
    result = runner.invoke(app, ["parse", str(notes_file), "--model", "anthropic", "--json"])
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert len(obj["requests"]) == 3
    actions = [r["action"] for r in obj["requests"]]
    assert "trim" in actions
    assert "flag" in actions


def test_export_with_scripted_model_roundtrips(
    notes_file: Path, tmp_path: Path, _patch_scripted_model: None
) -> None:
    out = tmp_path / "scripted"
    result = runner.invoke(
        app,
        ["export", str(notes_file), "--format", "both", "-o", str(out), "--json"],
    )
    assert result.exit_code == 0
    obj = _one_json_object(result.stdout)
    assert obj["changes"] == 3
    # Both deliverables read back cleanly.
    for p in obj["written"]:
        path = Path(p)
        if path.suffix == ".otio":
            otio.adapters.read_from_file(str(path))
        else:
            otio.adapters.read_from_file(str(path), "cmx_3600")
