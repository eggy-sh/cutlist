"""Unit tests for notes / Frame.io CSV ingestion."""

from __future__ import annotations

from pathlib import Path

import pytest

from cutlist.ingest import (
    SourceNote,
    load_frameio_csv,
    load_notes,
    merge_sources,
)


def test_load_notes_splits_on_blank_lines(notes_file: Path) -> None:
    notes = load_notes(notes_file)
    # SAMPLE_NOTES has four paragraphs.
    assert len(notes) == 4
    assert all(isinstance(n, SourceNote) for n in notes)
    assert all(n.source == "notes" for n in notes)
    assert notes[0].text.startswith("At 01:00:05:00")


def test_load_notes_drops_empty_fragments(tmp_path: Path) -> None:
    p = tmp_path / "notes.txt"
    p.write_text("first\n\n\n\nsecond\n\n   \n\nthird\n", encoding="utf-8")
    notes = load_notes(p)
    assert [n.text for n in notes] == ["first", "second", "third"]


def test_load_notes_accepts_str_path(notes_file: Path) -> None:
    notes = load_notes(str(notes_file))
    assert len(notes) == 4


def test_load_notes_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_notes(tmp_path / "nope.txt")


def test_load_frameio_csv_fuzzy_headers(frameio_csv_file: Path) -> None:
    notes = load_frameio_csv(frameio_csv_file)
    assert len(notes) == 2
    assert notes[0].text == "Punch in on the speaker here"
    # Comment ID column drives provenance.
    assert notes[0].source == "c-101"
    # Timecode column populates the hint.
    assert notes[0].timecode_hint == "01:00:05:00"
    assert notes[1].source == "c-102"


def test_load_frameio_csv_skips_textless_rows(tmp_path: Path) -> None:
    p = tmp_path / "fio.csv"
    p.write_text(
        "Comment ID,Timecode,Comment Text\n"
        "c-1,01:00:00:00,Good note\n"
        "c-2,01:00:01:00,\n"
        "c-3,01:00:02:00,   \n"
        "c-4,01:00:03:00,Another note\n",
        encoding="utf-8",
    )
    notes = load_frameio_csv(p)
    assert [n.text for n in notes] == ["Good note", "Another note"]
    assert [n.source for n in notes] == ["c-1", "c-4"]


def test_load_frameio_csv_fallback_source_when_no_id(tmp_path: Path) -> None:
    p = tmp_path / "fio.csv"
    p.write_text(
        "Timecode,Comment\n01:00:00:00,A note\n01:00:01:00,Another\n",
        encoding="utf-8",
    )
    notes = load_frameio_csv(p)
    assert notes[0].source == "frameio:1"
    assert notes[1].source == "frameio:2"
    assert notes[0].timecode_hint == "01:00:00:00"


def test_load_frameio_csv_no_timecode_column(tmp_path: Path) -> None:
    p = tmp_path / "fio.csv"
    p.write_text("Comment\nA note\n", encoding="utf-8")
    notes = load_frameio_csv(p)
    assert notes[0].timecode_hint is None


def test_load_frameio_csv_no_text_column_raises(tmp_path: Path) -> None:
    p = tmp_path / "fio.csv"
    p.write_text("Author,Timecode\nDirector,01:00:00:00\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_frameio_csv(p)


def test_load_frameio_csv_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_frameio_csv(tmp_path / "nope.csv")


def test_merge_sources_preserves_order() -> None:
    a = [SourceNote("a1"), SourceNote("a2")]
    b = [SourceNote("b1", source="comment")]
    c = [SourceNote("c1")]
    merged = merge_sources(a, b, c)
    assert [n.text for n in merged] == ["a1", "a2", "b1", "c1"]


def test_merge_sources_empty() -> None:
    assert merge_sources() == []
