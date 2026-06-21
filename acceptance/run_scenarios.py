"""Thin entry point: run every acceptance scenario and write EVIDENCE.md.

This is a convenience wrapper around :mod:`acceptance.test_acceptance`'s
standalone driver, so the suite can be run either as a pytest module
(``python -m pytest acceptance/test_acceptance.py``) or as a plain script
(``python acceptance/run_scenarios.py``). Both paths are hermetic — no network,
no live LLM — and both produce ``acceptance/EVIDENCE.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure this directory is importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_acceptance import _run_all_standalone  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_run_all_standalone())
