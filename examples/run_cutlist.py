#!/usr/bin/env python3
"""End-to-end ``cutlist`` walkthrough — fully hermetic (no network, no API key).

This mirrors what the ``cutlist`` CLI does, but in plain Python so you can see the
moving parts:

1. **Ingest** the review prose (``notes.txt``) and a Frame.io comment export
   (``frameio_comments.csv``) into a normalized list of source fragments.
2. **Extract** timecode-anchored change requests with a :class:`replykit.Agent`.
   The model here is the CLI's offline :class:`~cutlist.cli.HeuristicModel`, a
   deterministic stand-in that needs no API key — swap in
   ``replykit.AnthropicModel()`` (with ``ANTHROPIC_API_KEY`` set) for real
   semantic extraction.
3. **Export** the change list to both studio-interchange deliverables:
   OpenTimelineIO (``.otio``) and a CMX3600 EDL (``.edl``).

Run it::

    python examples/run_cutlist.py

It writes ``review.otio`` and ``review.edl`` into ``examples/build/`` (a
gitignored scratch directory) and prints the extracted changes plus replykit's
token/cost telemetry.
"""

from __future__ import annotations

from pathlib import Path

from cutlist import extract_changes, load_frameio_csv, load_notes, to_edl, to_otio
from cutlist.cli import HeuristicModel
from cutlist.ingest import merge_sources

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "build"
FPS = 24.0


def main() -> None:
    # 1. Ingest: notes prose + Frame.io comments, in order.
    notes = load_notes(HERE / "notes.txt")
    comments = load_frameio_csv(HERE / "frameio_comments.csv")
    sources = merge_sources(notes, comments)
    print(
        f"Ingested {len(sources)} source fragment(s) "
        f"({len(notes)} notes + {len(comments)} Frame.io comments).\n"
    )

    # 2. Extract: run the replykit agent. The offline HeuristicModel is hermetic;
    #    swap in replykit.AnthropicModel() for real, semantic extraction.
    captured: dict[str, object] = {}
    changes = extract_changes(
        sources,
        HeuristicModel(),
        FPS,
        title="review",
        on_run=lambda run: captured.update(run=run),
    )

    print(f"Extracted {len(changes)} change request(s) at {changes.fps:g} fps:")
    for i, req in enumerate(changes, start=1):
        if req.at is not None:
            anchor = req.at.to_string()
        else:
            anchor = f"{req.span.start.to_string()}-{req.span.end.to_string()}"
        print(f"  {i}. [{req.action}] {anchor}  (conf {req.confidence:.2f})  {req.rationale}")

    run = captured.get("run")
    if run is not None:
        print("\nTelemetry:", run.telemetry.as_dict())

    # 3. Export both deliverables into the scratch build dir.
    OUT_DIR.mkdir(exist_ok=True)
    otio_path = to_otio(changes, OUT_DIR / "review.otio")
    edl_path = to_edl(changes, OUT_DIR / "review.edl")
    print(f"\nWrote:\n  {otio_path}\n  {edl_path}")


if __name__ == "__main__":
    main()
