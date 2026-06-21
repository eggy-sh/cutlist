"""Hermetic, reproducible acceptance runner for ``cutlist``.

This module executes **every** scenario from ``acceptance/scenarios.md`` under
identical conditions and asserts each machine-checkable success criterion. It is
both:

* a **pytest module** — ``python -m pytest acceptance/test_acceptance.py`` runs
  all eleven scenarios as individual tests; and
* an **evidence recorder** — ``python acceptance/test_acceptance.py`` (or the
  thin ``run_scenarios.py`` wrapper) runs the same scenarios and writes
  ``acceptance/EVIDENCE.md`` capturing, per scenario: id, the exact
  command/inputs, a captured-output / artifact summary, and a PASS/FAIL verdict.

Hermeticity (non-negotiable):

* No network and no live LLM. The default backend is the offline
  :class:`cutlist.cli.HeuristicModel`. The two "semantic" scenarios (S8, S9)
  inject a :class:`replykit.ScriptedModel` by monkeypatching
  ``cutlist.cli._build_model`` — they never reach a real provider.
* The model is used **only** where genuine judgment lives; all
  timecode / format / CSV / action-coercion / exit-code / ``--json`` logic is
  deterministic, and that is what the criteria check.

Every scenario runs under the same conditions: the repo's own interpreter and
the bundled fixtures (``examples/notes.txt``, ``examples/frameio_comments.csv``),
with scratch inputs written under a per-run temp dir. The CLI is exercised
in-process through Typer's :class:`~typer.testing.CliRunner` (and, for the
human-mode S1 table that Rich would otherwise wrap, through a real subprocess
with a wide ``COLUMNS`` so the documented command's output is faithful).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from replykit import ScriptedModel
from typer.testing import CliRunner

from cutlist import cli
from cutlist.cli import app
from cutlist.models import ChangeList
from cutlist.timecode import Timecode, TimecodeError

# --------------------------------------------------------------------------- #
# Fixed, repo-relative locations (identical conditions across every scenario). #
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
NOTES = EXAMPLES / "notes.txt"
FRAMEIO = EXAMPLES / "frameio_comments.csv"
VENV_CUTLIST = REPO_ROOT / ".venv" / "bin" / "cutlist"
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python3"
EVIDENCE_PATH = Path(__file__).resolve().parent / "EVIDENCE.md"

#: The six anchors the offline heuristic finds in ``examples/notes.txt``.
NOTES_ANCHORS = {
    "01:00:05:00",
    "01:00:12:00",  # range start
    "01:00:30:00",
    "01:00:45:00",  # range start
    "01:01:10:00",
    "01:01:40:00",
}
#: The three *new* anchors the Frame.io CSV contributes (01:00:05:00 duplicates).
FRAMEIO_NEW_ANCHORS = {"01:00:22:00", "01:00:58:00", "01:01:25:00"}

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Evidence collection plumbing                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class Evidence:
    """Captured record for one scenario, written to ``EVIDENCE.md``."""

    sid: str
    capability: str
    title: str
    command: str
    checks: list[tuple[str, bool]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None

    def check(self, label: str, ok: bool) -> None:
        self.checks.append((label, bool(ok)))

    @property
    def passed(self) -> bool:
        return self.error is None and all(ok for _, ok in self.checks)


#: Module-level sink so both pytest runs and the __main__ driver collect evidence.
_EVIDENCE: dict[str, Evidence] = {}


def _record(ev: Evidence) -> Evidence:
    _EVIDENCE[ev.sid] = ev
    return ev


def _short(text: str, limit: int = 600) -> str:
    text = text.replace("\r", "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [+{len(text) - limit} chars truncated]"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _one_json_object(stdout: str) -> dict[str, Any]:
    """Assert stdout is exactly one non-blank line parsing as one JSON object."""
    nonblank = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(nonblank) == 1, f"expected exactly one stdout line, got {nonblank!r}"
    obj = json.loads(stdout)
    assert isinstance(obj, dict)
    return obj


def _anchor_of(req: dict[str, Any]) -> str | None:
    return req.get("at") or req.get("start")


def _patch_model(monkeypatch: pytest.MonkeyPatch, emissions: list[str]) -> None:
    """Inject a ScriptedModel for the duration of one in-process CLI invocation."""
    model = ScriptedModel(emissions)
    monkeypatch.setattr(cli, "_build_model", lambda backend, model_name: model)


def _run_subprocess(args: list[str], *, wide: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the installed ``cutlist`` console script as a real subprocess.

    Used only where a faithful terminal render matters (S1 human mode). Stays
    hermetic: the default backend is offline and no env enables a provider.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    if wide:
        env["COLUMNS"] = "200"
    return subprocess.run(
        [str(VENV_CUTLIST), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )


# --------------------------------------------------------------------------- #
# S1 — parse human mode renders table + telemetry (C1)                         #
# --------------------------------------------------------------------------- #


def test_s1_parse_human_table_and_telemetry() -> None:
    cmd = f"{VENV_CUTLIST} parse examples/notes.txt   (COLUMNS=200)"
    ev = _record(Evidence("S1", "C1", "parse human mode renders table + telemetry", cmd))
    # A real subprocess with a wide terminal: faithful to the documented command,
    # and wide enough that Rich does not wrap the en-dash range across columns.
    proc = _run_subprocess(["parse", "examples/notes.txt"], wide=True)
    out = proc.stdout
    ev.artifacts.append("stdout (head):\n" + _short(out, 900))

    ev.check("exit code == 0", proc.returncode == 0)
    for tok in ("action", "anchor", "rationale", "source"):
        ev.check(f"table header token {tok!r}", tok in out)
    ev.check("contains 'Telemetry'", "Telemetry" in out)
    ev.check("contains 'estimated cost (USD)'", "estimated cost (USD)" in out)
    ev.check("cost value $0.000000", "$0.000000" in out)
    ev.check("point anchor 01:00:05:00", "01:00:05:00" in out)
    ev.check("point anchor 01:00:30:00", "01:00:30:00" in out)
    ev.check("en-dash range 01:00:12:00–01:00:19:12", "01:00:12:00–01:00:19:12" in out)
    ev.check("title 'notes — 6 change(s) @ 24 fps'", "notes — 6 change(s) @ 24 fps" in out)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S2 — parse --json one object + lossless reload (C2)                          #
# --------------------------------------------------------------------------- #


def test_s2_parse_json_contract_and_reload() -> None:
    cmd = f"{VENV_CUTLIST} parse examples/notes.txt --json"
    ev = _record(Evidence("S2", "C2", "parse --json contract + lossless reload", cmd))

    result = runner.invoke(app, ["parse", str(NOTES), "--json"])
    ev.check("exit code == 0", result.exit_code == 0)

    obj = _one_json_object(result.stdout)
    ev.artifacts.append("json:\n" + _short(json.dumps(obj, indent=2), 700))
    ev.check("exactly one JSON object on stdout", True)
    ev.check("title == 'notes'", obj["title"] == "notes")
    ev.check("fps == 24.0", obj["fps"] == 24.0)
    ev.check("len(requests) == 6", len(obj["requests"]) == 6)

    xor_ok = all(
        (("at" in r) ^ ("start" in r and "end" in r)) and not ("at" in r and "start" in r)
        for r in obj["requests"]
    )
    ev.check("each request: flat 'at' XOR flat 'start'+'end'", xor_ok)
    ev.check("every action == 'flag'", all(r["action"] == "flag" for r in obj["requests"]))
    ev.check("every confidence == 0.5", all(r["confidence"] == 0.5 for r in obj["requests"]))
    ev.check("every source == 'notes'", all(r["source"] == "notes" for r in obj["requests"]))
    ev.check("telemetry.calls >= 1", obj["telemetry"]["calls"] >= 1)
    ev.check("telemetry.total_cost_usd == 0.0", obj["telemetry"]["total_cost_usd"] == 0.0)
    ev.check("errors == []", obj["errors"] == [])

    reloaded = ChangeList.from_dict(obj)
    ev.check("ChangeList.from_dict len == 6", len(reloaded) == 6)
    ev.check("ChangeList.from_dict fps == 24.0", reloaded.fps == 24.0)

    anchors = [_anchor_of(r) for r in obj["requests"]]
    ev.check("anchors == sorted(anchors)", anchors == sorted(anchors))

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S3 — export OTIO round-trips + carries cutlist metadata (C3)                 #
# --------------------------------------------------------------------------- #


def test_s3_export_otio_roundtrip_and_metadata(tmp_path: Path) -> None:
    out = tmp_path / "review"
    cmd = f"{VENV_CUTLIST} export examples/notes.txt --format otio -o {out} --json"
    ev = _record(Evidence("S3", "C3", "export OTIO round-trip + metadata", cmd))

    result = runner.invoke(
        app, ["export", str(NOTES), "--format", "otio", "-o", str(out), "--json"]
    )
    ev.check("exit code == 0", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.artifacts.append("json:\n" + _short(json.dumps(obj, indent=2), 400))

    ev.check("format == 'otio'", obj["format"] == "otio")
    ev.check("changes == 6", obj["changes"] == 6)
    written = obj["written"]
    one_path = len(written) == 1 and written[0].endswith(".otio")
    ev.check("written is one .otio path", one_path)
    path = Path(written[0])
    ev.check("written path exists", path.exists())

    tl = otio.adapters.read_from_file(str(path))
    ev.check("read_from_file succeeds", tl is not None)
    ev.check("len(tracks) == 1", len(tl.tracks) == 1)

    s1 = otio.adapters.write_to_string(tl, "otio_json")
    tl2 = otio.adapters.read_from_string(s1, "otio_json")
    s2 = otio.adapters.write_to_string(tl2, "otio_json")
    ev.check("round-trip byte-stable (s1 == s2)", s1 == s2)

    track = tl.tracks[0]
    markers = list(track.markers)
    clips = list(track.find_clips())
    ev.check("4 Markers", len(markers) == 4)
    ev.check("2 Clips", len(clips) == 2)
    ev.check("total 6 objects", len(markers) + len(clips) == 6)

    expected_keys = {"action", "confidence", "rationale", "source"}
    meta_ok = all(
        set(obj_.metadata.get("cutlist", {}).keys()) == expected_keys for obj_ in markers + clips
    )
    ev.check("every metadata['cutlist'] has exact keys", meta_ok)
    ev.artifacts.append(f"markers={len(markers)} clips={len(clips)} keys_ok={meta_ok}")

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S4 — export EDL parses via cmx_3600, byte-deterministic (C4)                 #
# --------------------------------------------------------------------------- #


def test_s4_export_edl_cmx3600_and_determinism(tmp_path: Path) -> None:
    out = tmp_path / "review"
    out2 = tmp_path / "review2"
    cmd = (
        f"{VENV_CUTLIST} export examples/notes.txt --format edl -o {out} --json "
        f"(then again to {out2})"
    )
    ev = _record(Evidence("S4", "C4", "export EDL cmx_3600 + byte-determinism", cmd))

    result = runner.invoke(app, ["export", str(NOTES), "--format", "edl", "-o", str(out), "--json"])
    ev.check("exit code == 0", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.check("format == 'edl'", obj["format"] == "edl")
    path = Path(obj["written"][0])
    ev.check("single .edl path", len(obj["written"]) == 1 and path.suffix == ".edl")
    ev.check("written path exists", path.exists())

    body = path.read_text()
    ev.artifacts.append("edl head:\n" + _short(body, 400))
    ev.check("body starts with 'TITLE: notes'", body.startswith("TITLE: notes"))
    ev.check("contains 'FCM: NON-DROP FRAME'", "FCM: NON-DROP FRAME" in body)

    tl = otio.adapters.read_from_file(str(path), "cmx_3600")
    ev.check("cmx_3600 parse non-None", tl is not None)
    clips = list(tl.find_clips())
    ev.check("6 events (clips)", len(clips) == 6)

    # Second export, byte-identical.
    result2 = runner.invoke(
        app, ["export", str(NOTES), "--format", "edl", "-o", str(out2), "--json"]
    )
    ev.check("second export exit 0", result2.exit_code == 0)
    body2 = Path(json.loads(result2.stdout)["written"][0]).read_text()
    ev.check("second export byte-identical (diff exit 0)", body == body2)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S5 — export both writes both deliverables (C5)                              #
# --------------------------------------------------------------------------- #


def test_s5_export_both(tmp_path: Path) -> None:
    out = tmp_path / "review"
    cmd = f"{VENV_CUTLIST} export examples/notes.txt --format both -o {out} --json"
    ev = _record(Evidence("S5", "C5", "export --format both writes both", cmd))

    result = runner.invoke(
        app, ["export", str(NOTES), "--format", "both", "-o", str(out), "--json"]
    )
    ev.check("exit code == 0", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.artifacts.append("written:\n" + _short(json.dumps(obj["written"], indent=2), 300))

    ev.check("format == 'both'", obj["format"] == "both")
    ev.check("changes == 6", obj["changes"] == 6)
    suffixes = sorted(Path(p).suffix for p in obj["written"])
    ev.check("suffixes == ['.edl', '.otio']", suffixes == [".edl", ".otio"])
    ev.check("both files exist", all(Path(p).exists() for p in obj["written"]))

    otio_path = next(p for p in obj["written"] if p.endswith(".otio"))
    edl_path = next(p for p in obj["written"] if p.endswith(".edl"))
    ev.check("otio reads via read_from_file", otio.adapters.read_from_file(otio_path) is not None)
    ev.check(
        "edl reads via cmx_3600", otio.adapters.read_from_file(edl_path, "cmx_3600") is not None
    )

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S6 — Frame.io CSV fold-in adds anchors (C6)                                  #
# --------------------------------------------------------------------------- #


def test_s6_frameio_fold_in() -> None:
    cmd = (
        f"{VENV_CUTLIST} parse examples/notes.txt --json   vs   "
        f"--frameio examples/frameio_comments.csv --json"
    )
    ev = _record(Evidence("S6", "C6", "Frame.io CSV folds in extra anchors", cmd))

    base = runner.invoke(app, ["parse", str(NOTES), "--json"])
    merged = runner.invoke(app, ["parse", str(NOTES), "--frameio", str(FRAMEIO), "--json"])
    ev.check("base exit 0", base.exit_code == 0)
    ev.check("merged exit 0", merged.exit_code == 0)

    base_obj = _one_json_object(base.stdout)
    merged_obj = _one_json_object(merged.stdout)
    ev.check(
        "len(merged) > len(base) (9 > 6)",
        len(merged_obj["requests"]) == 9 > len(base_obj["requests"]),
    )

    merged_anchors = {_anchor_of(r) for r in merged_obj["requests"]}
    ev.artifacts.append("merged anchors: " + ", ".join(sorted(a for a in merged_anchors if a)))
    ev.check("new anchors present", FRAMEIO_NEW_ANCHORS <= merged_anchors)
    ev.check("all six original notes anchors present", NOTES_ANCHORS <= merged_anchors)

    ChangeList.from_dict(merged_obj)
    ev.check("merged reloads via ChangeList.from_dict", True)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S7 — --fps changes frame base + deterministic timecode unit checks (C7)      #
# --------------------------------------------------------------------------- #


def test_s7_fps_and_timecode_units() -> None:
    cmd = f"{VENV_CUTLIST} parse examples/notes.txt --fps 30 --json  + Timecode unit checks"
    ev = _record(Evidence("S7", "C7", "fps frame base + Timecode determinism", cmd))

    result = runner.invoke(app, ["parse", str(NOTES), "--fps", "30", "--json"])
    ev.check("parse exit 0", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.check("fps == 30.0", obj["fps"] == 30.0)
    ChangeList.from_dict(obj)
    ev.check("reloads via ChangeList.from_dict (re-parse @30)", True)

    f24 = Timecode.from_string("01:00:05:00", 24).frame
    f30 = Timecode.from_string("01:00:05:00", 30).frame
    ev.artifacts.append(f"frame@24={f24}  frame@30={f30}")
    ev.check("frame@24 == 86520", f24 == 86520)
    ev.check("frame@30 == 108150", f30 == 108150)

    raised = False
    msg = ""
    try:
        Timecode.from_string("01:00:05;00", 24)
    except TimecodeError as exc:
        raised = True
        msg = str(exc)
    ev.artifacts.append(f"drop-frame error: {msg}")
    ev.check("drop-frame raises TimecodeError", raised)
    ev.check("message mentions 'drop-frame'", "drop-frame" in msg)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S8 — scripted model: action coercion + provenance pass-through (C8)          #
# --------------------------------------------------------------------------- #

_S8_EMISSIONS = [
    (
        "@reply name=emit_change\n"
        "action = tighten\n"
        "rationale = audio dips under the music; bring the VO up\n"
        "start = 01:00:12:00\n"
        "end = 01:00:19:12\n"
        "confidence = 0.9\n"
        "source = c-202\n"
        "@end"
    ),
    (
        "@reply name=emit_change\n"
        "action = warm\n"
        "rationale = warm the green back half to match the open\n"
        "at = 01:00:45:00\n"
        "confidence = 0.8\n"
        "source = notes\n"
        "@end"
    ),
    "Extracted 2 change requests.",
]


def test_s8_scripted_action_coercion_and_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = (
        "monkeypatch cli._build_model -> ScriptedModel(tighten/range/c-202, warm/point/notes); "
        f"{VENV_CUTLIST} parse examples/notes.txt --model anthropic --json"
    )
    ev = _record(Evidence("S8", "C8", "scripted action coercion + provenance", cmd))
    _patch_model(monkeypatch, _S8_EMISSIONS)

    result = runner.invoke(app, ["parse", str(NOTES), "--model", "anthropic", "--json"])
    ev.check("exit code == 0", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.artifacts.append("json:\n" + _short(json.dumps(obj["requests"], indent=2), 600))

    ev.check("len(requests) == 2", len(obj["requests"]) == 2)
    ev.check("errors == []", obj["errors"] == [])
    actions = [r["action"] for r in obj["requests"]]
    ev.check(
        "actions == ['trim', 'color'] (tighten->trim, warm->color)",
        actions == ["trim", "color"],
    )
    ev.check(
        "one source == 'c-202' (Frame.io id survives)",
        any(r["source"] == "c-202" for r in obj["requests"]),
    )

    range_reqs = [r for r in obj["requests"] if "start" in r and "end" in r]
    point_reqs = [r for r in obj["requests"] if "at" in r]
    ev.check("range request carries flat start/end", len(range_reqs) == 1)
    ev.check("point request carries flat at", len(point_reqs) == 1)

    ChangeList.from_dict(obj)
    ev.check("payload reloads via ChangeList.from_dict", True)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S9 — malformed note is skipped, surfaced, never fatal (C9)                   #
# --------------------------------------------------------------------------- #

_S9_EMISSIONS = [
    (
        "@reply name=emit_change\n"
        "action = flag\n"
        "rationale = valid point note\n"
        "at = 01:00:05:00\n"
        "confidence = 0.9\n"
        "source = notes\n"
        "@end"
    ),
    (
        "@reply name=emit_change\n"
        "action = flag\n"
        "rationale = out-of-range timecode\n"
        "at = 01:00:99:00\n"
        "confidence = 0.9\n"
        "source = notes\n"
        "@end"
    ),
    "Done.",
]


def test_s9_malformed_note_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = (
        "monkeypatch cli._build_model -> ScriptedModel(valid 01:00:05:00 + bad 01:00:99:00); "
        f"{VENV_CUTLIST} parse examples/notes.txt --model anthropic --json"
    )
    ev = _record(Evidence("S9", "C9", "malformed note skipped, not fatal", cmd))
    _patch_model(monkeypatch, _S9_EMISSIONS)

    result = runner.invoke(app, ["parse", str(NOTES), "--model", "anthropic", "--json"])
    ev.check("exit code == 0 (bad note not fatal)", result.exit_code == 0)
    obj = _one_json_object(result.stdout)
    ev.artifacts.append("json:\n" + _short(json.dumps(obj, indent=2), 500))

    ev.check("len(requests) == 1 (only valid survives)", len(obj["requests"]) == 1)
    ev.check("len(errors) == 1", len(obj["errors"]) == 1)
    err_ok = len(obj["errors"]) == 1 and "seconds field out of range" in obj["errors"][0]
    ev.check("error mentions offending field", err_ok)
    ev.check("stdout is exactly one JSON object", True)

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S10 — error contract: four failure modes + stream isolation (C10)           #
# --------------------------------------------------------------------------- #


def test_s10_error_contract(tmp_path: Path) -> None:
    nope = tmp_path / "nope.txt"
    empty = tmp_path / "empty.txt"
    empty.write_text("No timecodes anywhere, just feelings.\n", encoding="utf-8")
    cmd = (
        "four failure modes (missing file, unknown model, bad format, empty export) "
        "+ human isolation"
    )
    ev = _record(Evidence("S10", "C10", "error contract + stream isolation", cmd))

    # Missing notes file (JSON).
    r1 = runner.invoke(app, ["parse", str(nope), "--json"])
    o1 = _one_json_object(r1.stdout)
    ev.check("missing file: exit 1", r1.exit_code == 1)
    ev.check("missing file: error contains 'not found'", "not found" in o1["error"])

    # Unknown model.
    r2 = runner.invoke(app, ["parse", str(NOTES), "--model", "telepathy", "--json"])
    o2 = _one_json_object(r2.stdout)
    ev.check("unknown model: exit 1", r2.exit_code == 1)
    ev.check("unknown model: error contains 'unknown --model'", "unknown --model" in o2["error"])

    # Bad format.
    r3 = runner.invoke(
        app, ["export", str(NOTES), "--format", "xml", "-o", str(tmp_path / "x"), "--json"]
    )
    o3 = _one_json_object(r3.stdout)
    ev.check("bad format: exit 1", r3.exit_code == 1)
    ev.check("bad format: error contains 'unknown --format'", "unknown --format" in o3["error"])

    # Empty change list export.
    r4 = runner.invoke(
        app, ["export", str(empty), "--format", "otio", "-o", str(tmp_path / "e"), "--json"]
    )
    o4 = _one_json_object(r4.stdout)
    ev.check("empty export: exit 1", r4.exit_code == 1)
    ev.check("empty export: error contains 'empty ChangeList'", "empty ChangeList" in o4["error"])

    # Human-mode stdout/stderr isolation. In this Click/Typer version stderr is
    # captured separately by default (no mix_stderr arg needed); stdout must stay
    # empty and the diagnostic must go to stderr.
    r5 = runner.invoke(app, ["parse", str(nope)])
    ev.check("human missing file: exit 1", r5.exit_code == 1)
    ev.check("human missing file: stdout empty", r5.stdout.strip() == "")
    ev.check("human missing file: stderr contains 'not found'", "not found" in r5.stderr)

    ev.artifacts.append(
        "errors:\n"
        + "\n".join(
            [
                o1["error"],
                o2["error"],
                o3["error"],
                o4["error"],
                "human stderr: " + r5.stderr.strip(),
            ]
        )
    )

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# S11 — Frame.io CSV edge cases: fuzzy headers + no-text-column (C6 edge)      #
# --------------------------------------------------------------------------- #


def test_s11_frameio_csv_edge_cases(tmp_path: Path) -> None:
    weird = tmp_path / "weird.csv"
    weird.write_text(
        "Comment ID,TC,Body,Author\n"
        "x-1,01:00:10:00,Fix this shot,Director\n"
        "x-2,,General note with no timecode,Editor\n",
        encoding="utf-8",
    )
    notext = tmp_path / "notext.csv"
    notext.write_text("Foo,Bar\n1,2\n", encoding="utf-8")
    cmd = (
        f"load_frameio_csv({weird})  +  "
        f"{VENV_CUTLIST} parse examples/notes.txt --frameio {notext} --json"
    )
    ev = _record(Evidence("S11", "C6", "Frame.io CSV fuzzy headers + no-text failure", cmd))

    from cutlist.ingest import load_frameio_csv

    notes = load_frameio_csv(weird)
    ev.check("load_frameio_csv returns 2 notes", len(notes) == 2)
    if len(notes) == 2:
        ev.check("Body column selected as text", notes[0].text == "Fix this shot")
        ev.check("Comment ID becomes source (x-1)", notes[0].source == "x-1")
        ev.check("TC populates timecode_hint", notes[0].timecode_hint == "01:00:10:00")
        ev.check("blank TC -> None", notes[1].timecode_hint is None)
        ev.artifacts.append(
            "notes: "
            + "; ".join(f"({n.source!r}, text={n.text!r}, tc={n.timecode_hint!r})" for n in notes)
        )

    result = runner.invoke(app, ["parse", str(NOTES), "--frameio", str(notext), "--json"])
    o = _one_json_object(result.stdout)
    ev.check("no-text CSV: exit 1", result.exit_code == 1)
    ev.check(
        "no-text CSV: error contains 'no comment-text column found'",
        "no comment-text column found" in o["error"],
    )
    ev.artifacts.append("no-text error: " + o["error"])

    assert ev.passed, [c for c in ev.checks if not c[1]]


# --------------------------------------------------------------------------- #
# Evidence writer (runs after the pytest session, and from __main__)          #
# --------------------------------------------------------------------------- #


def _write_evidence() -> None:
    """Render the collected per-scenario evidence to ``acceptance/EVIDENCE.md``."""
    order = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"]
    present = [_EVIDENCE[s] for s in order if s in _EVIDENCE]
    total = len(present)
    passed = sum(1 for e in present if e.passed)

    lines: list[str] = []
    lines.append("# cutlist — Acceptance Evidence")
    lines.append("")
    lines.append(
        "Hermetic acceptance run of every scenario in `acceptance/scenarios.md`, "
        "executed under identical conditions (offline `HeuristicModel` default; "
        "`replykit.ScriptedModel` injected via monkeypatch for S8/S9; **no network, "
        "no live LLM**). Generated by `acceptance/test_acceptance.py`."
    )
    lines.append("")
    lines.append(f"- Interpreter: `{VENV_PY}`")
    lines.append(f"- CLI: `{VENV_CUTLIST}`")
    lines.append(f"- Fixtures: `{NOTES}`, `{FRAMEIO}`")
    lines.append("")
    lines.append(f"## Result: {passed}/{total} scenarios PASS")
    lines.append("")
    lines.append("| Scenario | Capability | Verdict |")
    lines.append("| --- | --- | --- |")
    for e in present:
        lines.append(f"| {e.sid} — {e.title} | {e.capability} | {'PASS' if e.passed else 'FAIL'} |")
    lines.append("")

    for e in present:
        lines.append(f"### {e.sid} — {e.title}  *({e.capability})*")
        lines.append("")
        lines.append(f"**Verdict:** {'PASS' if e.passed else 'FAIL'}")
        lines.append("")
        lines.append("**Command / inputs:**")
        lines.append("")
        lines.append("```")
        lines.append(e.command)
        lines.append("```")
        lines.append("")
        if e.error is not None:
            lines.append(f"**Runner error:** {e.error}")
            lines.append("")
        lines.append("**Checks:**")
        lines.append("")
        for label, ok in e.checks:
            lines.append(f"- [{'x' if ok else ' '}] {label}")
        lines.append("")
        if e.artifacts:
            lines.append("**Captured output / artifact summary:**")
            lines.append("")
            for art in e.artifacts:
                lines.append("```")
                lines.append(art)
                lines.append("```")
                lines.append("")
        lines.append("---")
        lines.append("")

    EVIDENCE_PATH.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="session", autouse=True)
def _emit_evidence_after_session():  # type: ignore[no-untyped-def]
    """Write EVIDENCE.md at the end of a pytest session (best-effort)."""
    yield
    try:
        _write_evidence()
    except Exception as exc:  # pragma: no cover - evidence is best-effort
        print(f"[acceptance] could not write evidence: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Standalone driver: run every scenario, write evidence, print a summary.      #
# --------------------------------------------------------------------------- #


def _run_all_standalone() -> int:
    """Run all scenarios without pytest, collect evidence, return exit code.

    Mirrors the pytest tests but drives them directly so the evidence file and a
    machine-readable summary are produced even when invoked as a plain script.
    """
    import tempfile

    class _MonkeyPatch:
        """Minimal setattr/undo shim mirroring pytest.MonkeyPatch."""

        def __init__(self) -> None:
            self._undo: list[tuple[Any, str, Any]] = []

        def setattr(self, target: Any, name: str, value: Any) -> None:
            self._undo.append((target, name, getattr(target, name)))
            setattr(target, name, value)

        def undo(self) -> None:
            for target, name, old in reversed(self._undo):
                setattr(target, name, old)
            self._undo.clear()

    scenarios: list[tuple[str, Callable[..., None], bool, bool]] = [
        # (id, fn, needs_tmp, needs_monkeypatch)
        ("S1", test_s1_parse_human_table_and_telemetry, False, False),
        ("S2", test_s2_parse_json_contract_and_reload, False, False),
        ("S3", test_s3_export_otio_roundtrip_and_metadata, True, False),
        ("S4", test_s4_export_edl_cmx3600_and_determinism, True, False),
        ("S5", test_s5_export_both, True, False),
        ("S6", test_s6_frameio_fold_in, False, False),
        ("S7", test_s7_fps_and_timecode_units, False, False),
        ("S8", test_s8_scripted_action_coercion_and_provenance, False, True),
        ("S9", test_s9_malformed_note_skipped, False, True),
        ("S10", test_s10_error_contract, True, False),
        ("S11", test_s11_frameio_csv_edge_cases, True, False),
    ]

    for sid, fn, needs_tmp, needs_mp in scenarios:
        mp = _MonkeyPatch()
        tmpdir: tempfile.TemporaryDirectory[str] | None = None
        try:
            kwargs: dict[str, Any] = {}
            if needs_tmp:
                tmpdir = tempfile.TemporaryDirectory()
                kwargs["tmp_path"] = Path(tmpdir.name)
            if needs_mp:
                kwargs["monkeypatch"] = mp
            fn(**kwargs)
        except AssertionError as exc:
            ev = _EVIDENCE.get(sid)
            if ev is not None and ev.passed:
                ev.error = f"assertion: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            ev = _EVIDENCE.get(sid)
            if ev is None:
                ev = _record(Evidence(sid, "?", "(scenario raised before recording)", ""))
            ev.error = f"{type(exc).__name__}: {exc}"
        finally:
            mp.undo()
            if tmpdir is not None:
                tmpdir.cleanup()

    _write_evidence()

    order = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11"]
    present = [_EVIDENCE[s] for s in order if s in _EVIDENCE]
    total = len(present)
    passed = sum(1 for e in present if e.passed)
    failures = [e.sid for e in present if not e.passed]

    print(f"\nAcceptance: {passed}/{total} scenarios PASS")
    for e in present:
        mark = "PASS" if e.passed else "FAIL"
        print(f"  {e.sid:>3}  {mark}  {e.title}")
        if not e.passed:
            for label, ok in e.checks:
                if not ok:
                    print(f"        - FAILED: {label}")
            if e.error:
                print(f"        - ERROR: {e.error}")
    print(f"\nEvidence written to {EVIDENCE_PATH}")
    print(json.dumps({"total": total, "passed": passed, "failures": failures}))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(_run_all_standalone())
