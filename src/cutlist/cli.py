"""The ``cutlist`` command-line interface (Typer + Rich).

Two commands, both automation-friendly:

* ``cutlist parse NOTES.txt`` — extract and display the structured change list.
* ``cutlist export NOTES.txt --format otio|edl|both --fps 24 -o out`` — write the
  studio-interchange deliverable(s).

Every command accepts ``--json`` and, when set, prints **exactly one JSON object**
to stdout and nothing else, so the tool drops cleanly into agent pipelines.
A ``--frameio FILE.csv`` option folds Frame.io comments into the same run, and
``--model`` selects the extraction backend.

This module owns no extraction/export logic — it is a thin shell over the
:mod:`cutlist` library (``load_notes`` / ``load_frameio_csv`` / ``merge_sources``
/ ``extract_changes`` / ``to_otio`` / ``to_edl``). The model defaults to a
hermetic, offline stand-in (:class:`HeuristicModel`) so the CLI is runnable and
testable with **no API key and no network**; a real provider (``anthropic`` /
``openai``) is strictly opt-in.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import typer
from replykit import Completion, Model
from rich.console import Console
from rich.table import Table

from .extract import extract_changes
from .ingest import SourceNote, load_frameio_csv, load_notes, merge_sources
from .models import ChangeList

app = typer.Typer(
    name="cutlist",
    help="Turn editor's-notes prose into timecode-anchored change requests; "
    "export to OpenTimelineIO + CMX3600 EDL.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)

# A timecode token: HH:MM:SS:FF (non-drop). Used only by the offline stand-in to
# find anchors in the prose; real extraction is the model's job.
_TC_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}:\d{2}\b")
# A range token: TC-TC (a hyphen or en dash between two timecodes).
_RANGE_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2}:\d{2}:\d{2})")


class _CliError(Exception):
    """A user-facing CLI configuration / input error."""


# ---------------------------------------------------------------------------
# The default hermetic stand-in model.
# ---------------------------------------------------------------------------


class HeuristicModel:
    """A deterministic, offline stand-in for a real LLM.

    The CLI must run with **no API key and no network**, yet still produce a
    meaningful change list. This model does *no* reasoning: on its first turn it
    scans the agent prompt for ``HH:MM:SS:FF`` timecodes (and ``TC-TC`` ranges),
    and emits one ``@reply name=emit_change`` block per anchor — a ranged note
    for ranges, a point note for lone timecodes — using the surrounding line as
    the rationale. Once the agent feeds tool results back (the prompt now
    contains ``"Tool results:"``), it returns a plain-text final answer, ending
    the agent loop.

    It is intentionally a *floor*, not a ceiling: it never invents timecodes the
    notes don't contain. A real provider (``--model anthropic``) does the
    semantic work of mapping prose verbs onto the action vocabulary. Being a
    plain :class:`replykit.Model`, it keeps the whole CLI hermetic and testable.
    """

    #: Reported as the model id for telemetry/cost (a local model: $0.00).
    model = "cutlist-heuristic"

    def complete(self, prompt: str, **opts: Any) -> Completion:
        if "Tool results:" in prompt:
            text = "Done — emitted one change per detected timecode anchor."
            return Completion(
                text=text, input_tokens=len(prompt.split()), output_tokens=len(text.split())
            )
        blocks = self._emit_blocks(prompt)
        if not blocks:
            text = "No timecode-anchored changes detected in the notes."
            return Completion(
                text=text, input_tokens=len(prompt.split()), output_tokens=len(text.split())
            )
        text = "\n".join(blocks)
        return Completion(
            text=text, input_tokens=len(prompt.split()), output_tokens=len(text.split())
        )

    def _emit_blocks(self, prompt: str) -> list[str]:
        blocks: list[str] = []
        seen_spans: set[tuple[str, str]] = set()
        seen_points: set[str] = set()
        for line in self._note_lines(prompt):
            stripped = line.strip()
            if not stripped:
                continue
            range_match = _RANGE_RE.search(stripped)
            if range_match:
                start, end = range_match.group(1), range_match.group(2)
                if (start, end) in seen_spans:
                    continue
                seen_spans.add((start, end))
                rationale = self._rationale(stripped, range_match.group(0))
                blocks.append(
                    "@reply name=emit_change\n"
                    "action = flag\n"
                    f"rationale = {rationale}\n"
                    f"start = {start}\n"
                    f"end = {end}\n"
                    "confidence = 0.5\n"
                    "source = notes\n"
                    "@end"
                )
                continue
            point_match = _TC_RE.search(stripped)
            if point_match:
                tc = point_match.group(0)
                if tc in seen_points:
                    continue
                seen_points.add(tc)
                rationale = self._rationale(stripped, tc)
                blocks.append(
                    "@reply name=emit_change\n"
                    "action = flag\n"
                    f"rationale = {rationale}\n"
                    f"at = {tc}\n"
                    "confidence = 0.5\n"
                    "source = notes\n"
                    "@end"
                )
        return blocks

    @staticmethod
    def _note_lines(prompt: str) -> list[str]:
        """Return only the lines that are actual review notes.

        The agent prompt is ``<tool description>\\n\\nTask: <build_prompt>``. The
        injected tool description contains an *example* timecode, and the
        build_prompt preamble teaches the ``HH:MM:SS:FF`` format — neither is a
        real note. ``build_prompt`` lists the notes under a ``Notes:`` header, so
        we scope the scan to the lines after the **last** ``Notes:`` marker. If
        no marker is found (a non-default prompt shape), fall back to scanning
        everything, minus obvious instructional lines.
        """
        lines = prompt.splitlines()
        marker = None
        for i, line in enumerate(lines):
            if line.strip().lower() == "notes:":
                marker = i
        if marker is not None:
            return lines[marker + 1 :]
        # Fallback: drop instructional / template lines that mention the format.
        return [
            ln
            for ln in lines
            if "HH:MM:SS:FF" not in ln and "e.g." not in ln and not ln.lstrip().startswith("#")
        ]

    @staticmethod
    def _rationale(line: str, anchor: str) -> str:
        """Use the surrounding prose (with the anchor removed) as the rationale."""
        text = line.replace(anchor, "")
        # Drop build_prompt's leading "N. (source) " provenance prefix, if present.
        text = re.sub(r"^\s*\d+\.\s*\([^)]*\)\s*", "", text)
        # Drop a now-empty "[timecode hint: ]" label (its TC was the anchor).
        text = re.sub(r"\[timecode hint:[^\]]*\]", "", text)
        # Trim leading/trailing whitespace and stray punctuation/dashes.
        text = re.sub(r"^[\s\-–—:.,]+|[\s\-–—:.,]+$", "", text)
        text = re.sub(r"\s+", " ", text)
        if not text:
            text = "review note at this timecode"
        # Keep it to a single line; the protocol's value runs to EOL.
        return text[:200]


def _build_model(backend: str, model_name: str) -> Model:
    """Resolve a ``--model`` choice into a concrete :class:`replykit.Model`.

    ``heuristic`` is the hermetic default (no network). ``anthropic`` / ``openai``
    import their replykit adapter lazily and surface a clear :class:`_CliError`
    when the SDK or API key is absent — never a bare traceback.
    """
    backend = backend.lower()
    if backend in ("heuristic", "mock", "offline", "default"):
        return HeuristicModel()
    if backend == "anthropic":
        from replykit import AnthropicModel, MissingDependencyError

        try:
            return AnthropicModel(model=model_name)
        except MissingDependencyError as exc:  # pragma: no cover - needs SDK absence
            raise _CliError(str(exc)) from exc
    if backend == "openai":
        from replykit import MissingDependencyError, OpenAIModel

        try:
            return OpenAIModel(model=model_name)
        except MissingDependencyError as exc:  # pragma: no cover - needs SDK absence
            raise _CliError(str(exc)) from exc
    raise _CliError(f"unknown --model {backend!r}; choose heuristic|anthropic|openai")


def _load_sources(notes: str, frameio: str | None) -> list[SourceNote]:
    """Read the notes file (and optional Frame.io CSV) into source fragments."""
    notes_path = Path(notes)
    if not notes_path.exists():
        raise _CliError(f"notes file not found: {notes}")
    groups = [load_notes(notes_path)]
    if frameio:
        fio_path = Path(frameio)
        if not fio_path.exists():
            raise _CliError(f"Frame.io CSV not found: {frameio}")
        try:
            groups.append(load_frameio_csv(fio_path))
        except ValueError as exc:
            raise _CliError(f"could not read Frame.io CSV: {exc}") from exc
    return merge_sources(*groups)


def _telemetry_payload(holder: dict[str, Any]) -> dict[str, Any]:
    """Pull a JSON-ready telemetry dict out of the captured RunResult, if any."""
    run = holder.get("run")
    if run is None:
        return {}
    return run.telemetry.as_dict()


def _run_extraction(
    notes: str,
    fps: float,
    frameio: str | None,
    model: str,
    model_name: str,
    max_steps: int,
) -> tuple[ChangeList, dict[str, Any], list[str]]:
    """Shared pipeline: ingest -> extract. Returns (changes, telemetry, errors).

    We capture the raw :class:`replykit.RunResult` via the library's ``on_run``
    hook to expose token/cost telemetry, and to surface per-note extraction
    failures. The ``emit_change`` tool never raises — on a bad timecode/anchor it
    records the failure and returns an error string — so those failures show up
    as tool-call results in the run trace, which we collect for the caller.
    """
    sources = _load_sources(notes, frameio)
    backend = _build_model(model, model_name)
    holder: dict[str, Any] = {}

    def _on_run(run: Any) -> None:
        holder["run"] = run

    changes = extract_changes(
        sources,
        backend,
        fps,
        title=Path(notes).stem or "cutlist",
        max_steps=max_steps,
        on_run=_on_run,
    )
    telemetry = _telemetry_payload(holder)
    errors = _collect_errors(holder.get("run"))
    return changes, telemetry, errors


def _collect_errors(run: Any) -> list[str]:
    """Pull per-note ``emit_change`` failures off the run trace, best-effort.

    The sink's ``emit`` returns a short error string (e.g. starting with
    ``"error"``) instead of raising on a malformed note. Those land as the result
    of an ``emit_change`` tool call in the trace; we report them so a bad note is
    visible rather than silently lost.
    """
    if run is None:
        return []
    errors: list[str] = []
    for call in getattr(run, "trace", []):
        if call.tool != "emit_change":
            continue
        result = call.result
        if isinstance(result, str) and result.strip().lower().startswith("error"):
            errors.append(result.strip())
    return errors


def _fail(message: str, *, json_out: bool) -> None:
    """Report an error honoring the automation contract, then exit non-zero."""
    if json_out:
        # Keep the contract: exactly one JSON object on stdout.
        sys.stdout.write(json.dumps({"error": message}) + "\n")
    else:
        # Render the prefix with markup but the (possibly bracket-laden) message
        # literally, so a path/value never gets mis-parsed as Rich markup.
        err_console.print("[red]error:[/red]", end=" ")
        err_console.print(message, markup=False, highlight=False)
    raise typer.Exit(code=1)


def _emit_json(payload: dict[str, Any]) -> None:
    """Print exactly one JSON object to stdout (machine mode)."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def parse(
    notes: str = typer.Argument(..., help="Path to a free-form notes .txt file."),
    fps: float = typer.Option(24.0, "--fps", help="Frame rate for timecode parsing."),
    frameio: str | None = typer.Option(
        None, "--frameio", help="Optional Frame.io comment-export CSV to fold in."
    ),
    model: str = typer.Option(
        "heuristic",
        "--model",
        "-m",
        help="Extraction backend: heuristic (offline default) | anthropic | openai.",
    ),
    model_name: str = typer.Option(
        "claude-opus-4-8",
        "--name",
        help="Provider model id (for --model anthropic/openai + cost estimate).",
    ),
    max_steps: int = typer.Option(16, "--max-steps", help="Hard cap on extraction agent steps."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit exactly one JSON object to stdout (machine mode)."
    ),
) -> None:
    """Extract change requests from NOTES and show them (table, or one JSON object)."""
    try:
        changes, telemetry, errors = _run_extraction(
            notes, fps, frameio, model, model_name, max_steps
        )
    except _CliError as exc:
        _fail(str(exc), json_out=json_out)
        return

    if json_out:
        payload: dict[str, Any] = changes.to_dict()
        payload["telemetry"] = telemetry
        payload["errors"] = errors
        _emit_json(payload)
        return

    _render_changes(changes)
    for err in errors:
        err_console.print(f"[yellow]skipped note:[/yellow] {err}")
    _render_telemetry(telemetry)


@app.command()
def export(
    notes: str = typer.Argument(..., help="Path to a free-form notes .txt file."),
    fmt: str = typer.Option(
        "otio", "--format", "-f", help="Output format: 'otio', 'edl', or 'both'."
    ),
    fps: float = typer.Option(24.0, "--fps", help="Frame rate for timecode parsing."),
    out: str = typer.Option(
        "cutlist", "--output", "-o", help="Output path stem (extension added per format)."
    ),
    frameio: str | None = typer.Option(
        None, "--frameio", help="Optional Frame.io comment-export CSV to fold in."
    ),
    model: str = typer.Option(
        "heuristic",
        "--model",
        "-m",
        help="Extraction backend: heuristic (offline default) | anthropic | openai.",
    ),
    model_name: str = typer.Option(
        "claude-opus-4-8",
        "--name",
        help="Provider model id (for --model anthropic/openai + cost estimate).",
    ),
    max_steps: int = typer.Option(16, "--max-steps", help="Hard cap on extraction agent steps."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit exactly one JSON object to stdout (machine mode)."
    ),
) -> None:
    """Extract from NOTES and write the OTIO and/or EDL deliverable(s)."""
    fmt_norm = fmt.lower()
    if fmt_norm not in ("otio", "edl", "both"):
        _fail(f"unknown --format {fmt!r}; choose otio|edl|both", json_out=json_out)
        return

    try:
        changes, telemetry, errors = _run_extraction(
            notes, fps, frameio, model, model_name, max_steps
        )
    except _CliError as exc:
        _fail(str(exc), json_out=json_out)
        return

    # Import the writers lazily so a CLI error path never pays for OTIO import.
    from .edl_export import to_edl
    from .otio_export import to_otio

    written: list[str] = []
    try:
        if fmt_norm in ("otio", "both"):
            written.append(str(to_otio(changes, f"{out}.otio")))
        if fmt_norm in ("edl", "both"):
            written.append(str(to_edl(changes, f"{out}.edl")))
    except ValueError as exc:
        # Empty change list (or other writer guard) — report cleanly.
        _fail(str(exc), json_out=json_out)
        return

    if json_out:
        _emit_json(
            {
                "format": fmt_norm,
                "fps": fps,
                "title": changes.title,
                "changes": len(changes),
                "written": written,
                "telemetry": telemetry,
                "errors": errors,
            }
        )
        return

    console.print(
        f"[bold green]Wrote[/bold green] {len(changes)} change(s) "
        f"[dim]({fmt_norm}, {fps:g} fps)[/dim]:"
    )
    for path in written:
        console.print(f"  • {path}")
    for err in errors:
        err_console.print(f"[yellow]skipped note:[/yellow] {err}")
    _render_telemetry(telemetry)


# ---------------------------------------------------------------------------
# Rich renderers (human mode only)
# ---------------------------------------------------------------------------


def _render_changes(changes: ChangeList) -> None:
    """Render a change list as a Rich table."""
    if len(changes) == 0:
        console.print("[yellow]No change requests extracted.[/yellow]")
        return
    table = Table(
        title=f"{changes.title} — {len(changes)} change(s) @ {changes.fps:g} fps",
        title_justify="left",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("action", style="cyan")
    table.add_column("anchor")
    table.add_column("conf", justify="right")
    table.add_column("rationale")
    table.add_column("source", style="dim")
    for i, req in enumerate(changes, start=1):
        anchor = _anchor_text(req)
        table.add_row(
            str(i),
            str(req.action),
            anchor,
            f"{req.confidence:.2f}",
            req.rationale,
            req.source,
        )
    console.print(table)


def _anchor_text(req: Any) -> str:
    """Human-readable anchor: a point timecode or a range, from the request."""
    if req.at is not None:
        return req.at.to_string()
    if req.span is not None:
        return f"{req.span.start.to_string()}–{req.span.end.to_string()}"
    return "?"


def _render_telemetry(telemetry: dict[str, Any]) -> None:
    """Render the replykit token/cost telemetry as a small Rich table."""
    if not telemetry:
        return
    table = Table(title="Telemetry", title_justify="left")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("model calls", str(telemetry.get("calls", 0)))
    table.add_row("input tokens", str(telemetry.get("total_input_tokens", 0)))
    table.add_row("output tokens", str(telemetry.get("total_output_tokens", 0)))
    table.add_row("repair attempts", str(telemetry.get("total_repair_attempts", 0)))
    cost = telemetry.get("total_cost_usd", 0.0)
    table.add_row("estimated cost (USD)", f"${cost:.6f}")
    console.print(table)


def main() -> None:
    """Console-script entry point (``cutlist``). Delegates to the Typer app."""
    app()


if __name__ == "__main__":
    main()
