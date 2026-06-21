# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-20

Initial public release. `cutlist` turns free-form director/editor review prose
(and, optionally, a Frame.io comment CSV) into timecode-anchored change requests
and exports them to OpenTimelineIO and CMX3600 EDL.

### Added

- `timecode`: deterministic, frame-rate-aware `HH:MM:SS:FF` parsing/formatting
  (`Timecode`, `TimeRange`, `parse_timecode`, `format_timecode`). Non-drop-frame
  only — drop-frame (`;`) input is rejected with a clear `TimecodeError`.
- `models`: the `ChangeRequest` / `ChangeList` records and the closed `Action`
  vocabulary, with a synonym table that coerces model verbs onto the enum
  deterministically, plus lossless `to_dict` / `from_dict` round-tripping.
- `ingest`: read notes prose (`load_notes`) and Frame.io comment exports
  (`load_frameio_csv`) with tolerant/fuzzy header matching and per-comment
  provenance (`source`, `timecode_hint`).
- `extract`: a single-pass `replykit.Agent` (the `emit_change` tool) that turns
  prose into structured change requests; the model decides only *what* a note
  means, never *where* it lands. Ships an offline `HeuristicModel` default so the
  tool and its whole test suite run hermetically with no API key or network.
- `otio_export` (`to_otio`): change list → OpenTimelineIO timeline; point notes
  become Markers and range notes become Clips, each stamped with the
  `metadata["cutlist"]` namespace (`action`, `confidence`, `rationale`,
  `source`). Verified by round-trip through `opentimelineio`.
- `edl_export` (`to_edl`): change list → byte-deterministic CMX3600 EDL that
  parses back through OTIO's `cmx_3600` adapter.
- `cli`: a Typer + Rich command-line interface (the `cutlist` console script)
  with `parse` and `export` commands. Both support `--json` (exactly one JSON
  object on stdout for automation), `--fps`, `--frameio`, and `--model`; errors
  emit a clean `{"error": ...}` with exit code 1 and strict stdout/stderr
  isolation in human mode.
- Packaging: PEP 621 `pyproject.toml`, `src/` layout, typed (`py.typed`) public
  API surface in `cutlist/__init__.py`, runnable hermetic `examples/`, and a
  CI matrix (Python 3.11 / 3.12) running ruff, pytest + coverage, and an
  example smoke run with OTIO/EDL read-back.

[0.1.0]: https://github.com/edgarh92/cutlist/releases/tag/v0.1.0
