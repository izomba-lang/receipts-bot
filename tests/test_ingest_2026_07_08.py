from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ingest_2026_07_08 import (
    ReconciliationError,
    build_notes,
    content_type_for,
)


def test_notes_start_with_the_hash_marker_dedup_greps_for():
    notes = build_notes("abc123", "Поездка в Пулково", "фискальный чек")
    assert notes.startswith("sha256:abc123")
    assert "sha256:abc123" in notes
    assert notes == "sha256:abc123 · Поездка в Пулково · источник: фискальный чек"


def test_notes_survive_missing_note_and_source():
    assert build_notes("abc123", None, None) == "sha256:abc123"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.pdf", "application/pdf"), ("b.JPG", "image/jpeg"), ("c.jpeg", "image/jpeg")],
)
def test_content_type_by_extension(name, expected):
    assert content_type_for(Path(name)) == expected


def test_unknown_extension_is_refused_rather_than_guessed():
    with pytest.raises(ReconciliationError):
        content_type_for(Path("scan.heic"))
