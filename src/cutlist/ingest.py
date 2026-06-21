"""Read editorial notes prose and optional Frame.io comment CSV into raw text.

The extractor wants a single block of source text plus light provenance. This
module owns the two input shapes:

* **Notes prose** — a free-form ``.txt`` of director/editor review notes.
* **Frame.io CSV** — an exported comment list. Frame.io's export columns vary by
  account/version, so the reader is tolerant: it locates the comment-text and
  timecode columns by fuzzy header match and skips rows with no usable text.

Both produce a normalized list of :class:`SourceNote` fragments the extractor
concatenates into its prompt. Only the Python stdlib ``csv`` module is used — no
third-party CSV dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

#: Header substrings (lower-cased) that identify the comment-text column, most
#: specific first so "comment text" wins over a bare "comment" (which an id
#: column like "Comment ID" would otherwise also match). The id column is
#: excluded from text-column selection entirely (see :func:`load_frameio_csv`).
_TEXT_HEADER_HINTS = ("comment text", "text", "note", "message", "body", "comment")

#: Header substrings that identify a timecode column.
_TC_HEADER_HINTS = ("timecode", "tc", "time")

#: Header substrings that identify a stable comment id used as provenance.
_ID_HEADER_HINTS = ("comment id", "id")


@dataclass(frozen=True)
class SourceNote:
    """One raw note fragment with provenance, prior to extraction.

    ``text`` is the human prose. ``source`` identifies origin ("notes" for the
    prose file, or a Frame.io comment id / row index). ``timecode_hint`` carries
    a timecode the source supplied out-of-band (Frame.io's own TC column), which
    the extractor may use to anchor a note whose prose omits an explicit TC.
    """

    text: str
    source: str = "notes"
    timecode_hint: str | None = None


def load_notes(path: str | Path) -> list[SourceNote]:
    """Read a free-form notes ``.txt`` into source fragments.

    The file is split into note fragments on blank lines (paragraph breaks), each
    becoming one :class:`SourceNote` with ``source="notes"``. Empty fragments are
    dropped. Encoding is UTF-8.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"notes file not found: {p}")
    raw = p.read_text(encoding="utf-8")
    notes: list[SourceNote] = []
    for fragment in raw.split("\n\n"):
        text = fragment.strip()
        if text:
            notes.append(SourceNote(text=text, source="notes"))
    return notes


def _find_column(
    fieldnames: list[str],
    hints: tuple[str, ...],
    *,
    exclude: set[str] | None = None,
) -> str | None:
    """Return the first header whose lower-cased name contains any hint.

    Hints are tried in priority order; for each hint, headers are scanned in file
    order, so the first hint wins across all columns before the next hint is
    tried. ``exclude`` drops already-claimed columns (e.g. a "Comment ID" column
    already matched as the id) from consideration, keeping selection deterministic
    and non-overlapping.
    """
    excluded = exclude or set()
    lowered = [(name, name.strip().lower()) for name in fieldnames if name not in excluded]
    for hint in hints:
        for original, low in lowered:
            if hint in low:
                return original
    return None


def load_frameio_csv(path: str | Path) -> list[SourceNote]:
    """Read a Frame.io comment-export CSV into source fragments.

    Tolerant of column-name variation: the comment-text column is matched by
    headers like ``comment``/``text``/``note``; the timecode column by
    ``timecode``/``tc``/``time``. Rows with no comment text are skipped. Each
    surviving row yields a :class:`SourceNote` whose ``source`` is the row's
    comment id (or ``frameio:<row>`` fallback) and whose ``timecode_hint`` is the
    row's timecode if present.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If no comment-text column can be located in the header.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Frame.io CSV not found: {p}")
    with p.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        # Claim the id column first so it is excluded from text-column matching
        # ("Comment ID" must not be mistaken for the "Comment Text" column).
        id_col = _find_column(fieldnames, _ID_HEADER_HINTS)
        text_col = _find_column(
            fieldnames,
            _TEXT_HEADER_HINTS,
            exclude={id_col} if id_col else None,
        )
        if text_col is None:
            raise ValueError(f"no comment-text column found in CSV header {fieldnames!r}")
        tc_col = _find_column(fieldnames, _TC_HEADER_HINTS)
        notes: list[SourceNote] = []
        for row_index, row in enumerate(reader, start=1):
            text = (row.get(text_col) or "").strip()
            if not text:
                continue
            comment_id = (row.get(id_col) or "").strip() if id_col else ""
            source = comment_id or f"frameio:{row_index}"
            hint = (row.get(tc_col) or "").strip() if tc_col else ""
            notes.append(
                SourceNote(
                    text=text,
                    source=source,
                    timecode_hint=hint or None,
                )
            )
    return notes


def merge_sources(*groups: list[SourceNote]) -> list[SourceNote]:
    """Concatenate several source-note groups into one ordered list.

    Order is preserved group-by-group (notes first, then CSV, by call order).
    """
    merged: list[SourceNote] = []
    for group in groups:
        merged.extend(group)
    return merged
