"""Shared, hermetic test fixtures for the cutlist suite.

Everything here is deterministic and offline: no network, no live LLM, no real
files outside ``tmp_path``. The extraction tests drive a
:class:`replykit.ScriptedModel` whose responses are pre-written ``@reply`` blocks
calling the ``emit_change`` tool, so the agent loop runs end-to-end with no SDK.

These fixtures are the contract both SWE-Core (unit tests) and SWE-CLI
(integration tests) build on; keep them stable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Pin a wide terminal width so Rich-rendered CLI "--help" output is never
# wrapped/truncated under CI's narrow non-TTY width (which hides option flags
# from the help tests). Set at import time, before any CLI is imported/invoked.
os.environ["COLUMNS"] = "200"

# ---------------------------------------------------------------------------
# Canonical sample inputs
# ---------------------------------------------------------------------------

#: A small free-form notes file exercising a point note, a range note, a
#: normalized verb, and a low-confidence/fuzzy note.
SAMPLE_NOTES = """\
At 01:00:05:00 punch in on the speaker's face, the wide feels cold here.

Tighten 01:00:12:00-01:00:19:12 — the pause drags, lose about four seconds.

Around 01:00:30:00 the lower-third title is misspelled, fix it.

The color in the back half feels green, maybe warm it up (not sure where exactly).
"""

#: A minimal Frame.io comment-export CSV. Column names intentionally differ from
#: the obvious ones to exercise the tolerant header matcher.
SAMPLE_FRAMEIO_CSV = """\
Comment ID,Timecode,Comment Text,Author
c-101,01:00:05:00,Punch in on the speaker here,Director
c-102,01:00:40:00,Add a fade to black at the end,Editor
"""


@pytest.fixture
def fps() -> float:
    """The canonical test frame rate (non-drop 24fps)."""
    return 24.0


@pytest.fixture
def sample_notes_text() -> str:
    """The raw sample notes prose."""
    return SAMPLE_NOTES


@pytest.fixture
def notes_file(tmp_path: Path) -> Path:
    """A written sample notes ``.txt`` on disk."""
    p = tmp_path / "notes.txt"
    p.write_text(SAMPLE_NOTES, encoding="utf-8")
    return p


@pytest.fixture
def frameio_csv_file(tmp_path: Path) -> Path:
    """A written sample Frame.io comment-export CSV on disk."""
    p = tmp_path / "frameio.csv"
    p.write_text(SAMPLE_FRAMEIO_CSV, encoding="utf-8")
    return p


@pytest.fixture
def scripted_emissions() -> list[str]:
    """Pre-written ``@reply`` turns calling ``emit_change``, then a final answer.

    These match the ``emit_change`` tool signature in :mod:`cutlist.extract`
    (action, rationale, at | start/end, confidence, source). The trailing
    plain-text turn (no ``@reply`` block) is what replykit's Agent treats as the
    final answer, ending the loop.

    SWE-Core: feed this to ``replykit.ScriptedModel`` to drive ``extract_changes``
    deterministically. If you change the ``emit_change`` signature, update these.
    """
    return [
        (
            "@reply name=emit_change\n"
            "action = trim\n"
            "rationale = punch in on the speaker's face\n"
            "at = 01:00:05:00\n"
            "confidence = 0.9\n"
            "source = notes\n"
            "@end"
        ),
        (
            "@reply name=emit_change\n"
            "action = trim\n"
            "rationale = the pause drags, lose ~4s\n"
            "start = 01:00:12:00\n"
            "end = 01:00:19:12\n"
            "confidence = 0.95\n"
            "source = notes\n"
            "@end"
        ),
        (
            "@reply name=emit_change\n"
            "action = flag\n"
            "rationale = lower-third title misspelled\n"
            "at = 01:00:30:00\n"
            "confidence = 0.8\n"
            "source = notes\n"
            "@end"
        ),
        "Extracted 3 change requests.",
    ]


@pytest.fixture
def scripted_model(scripted_emissions: list[str]):
    """A :class:`replykit.ScriptedModel` preloaded with :func:`scripted_emissions`."""
    from replykit import ScriptedModel

    return ScriptedModel(scripted_emissions)
