# Contributing to cutlist

Thanks for helping build `cutlist`. This guide covers local setup, the test
philosophy, and the file-ownership split that lets two people work concurrently.

## Setup

`cutlist` depends on the local [`replykit`](https://github.com/edgarh92/replykit)
engine. Install it editable first, then cutlist's dev extras:

```bash
uv venv
uv pip install -e '../replykit'
uv pip install -e '.[dev]'
.venv/bin/python -c "import replykit, cutlist"   # sanity check
```

## Test philosophy: hermetic, always

Every test runs **offline** — no network, no live LLM, no real cloud files.

- Extraction tests drive a `replykit.ScriptedModel` whose responses are
  pre-written `@reply` blocks (see `tests/conftest.py`). The agent loop runs
  end-to-end with no SDK installed.
- Export tests assert **interop correctness**, not just string equality: OTIO
  output must round-trip through `opentimelineio`, and EDL output must parse via
  OTIO's `cmx_3600` adapter.
- Timecode tests are pure and deterministic.

Run the suite:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest --cov=cutlist --cov-report=term-missing
```

## File ownership (parallel work)

To avoid merge collisions, the work is partitioned into two non-overlapping sets.
**Do not edit files outside your set, and neither set edits `pyproject.toml`.**

- **SWE-Core** owns the library + unit tests:
  `src/cutlist/{timecode,models,ingest,extract,otio_export,edl_export}.py` and
  `tests/test_*.py` for those modules.
- **SWE-CLI** owns the shell + integration:
  `src/cutlist/cli.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`,
  the README body, and `examples/`.

See the project spec for exact paths and the module public APIs both sides code
against.

## Style

- Ruff is the linter/formatter (config in `pyproject.toml`, line length 100).
- Type hints on all public functions; `from __future__ import annotations` at the
  top of every module.
- Public functions get docstrings. Keep the `__init__.py` `__all__` in sync with
  what you add.

## Commits & PRs

- Conventional, present-tense commit subjects (`add edl event formatter`).
- Keep PRs scoped to one ownership set where possible.
- CI (ruff + pytest on Python 3.11 and 3.12) must be green before merge.
