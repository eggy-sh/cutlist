# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffold: PEP 621 `pyproject.toml`, `src/` layout, public API surface
  in `cutlist/__init__.py`, stub modules, test fixtures, and docs.
- Module contracts (signatures + docstrings) for `timecode`, `models`, `ingest`,
  `extract`, `otio_export`, `edl_export`, and the `cutlist` CLI.

## [0.1.0] - TBD

Initial release (planned):
- Extract timecode-anchored change requests from notes prose + Frame.io CSV via
  a `replykit` agent.
- Deterministic, frame-rate-aware `HH:MM:SS:FF` timecode parsing.
- Export to OpenTimelineIO (`.otio`) and CMX3600 EDL (`.edl`).
- `cutlist parse` / `cutlist export` CLI, with `--json` machine output.

[Unreleased]: https://github.com/eggy-sh/cutlist/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eggy-sh/cutlist/releases/tag/v0.1.0
