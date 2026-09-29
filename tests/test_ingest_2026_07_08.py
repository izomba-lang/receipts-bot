from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from scripts.ingest_2026_07_08 import (
    EXPECTED_CURRENCY_TOTALS,
    ReconciliationError,
    build_notes,
    content_type_for,
    expectations_for,
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


def test_manifest_without_expect_block_keeps_the_2026_07_08_targets():
    expect = expectations_for({"receipts": []})
    assert expect["rows"] == 32
    assert expect["currency_totals"] == EXPECTED_CURRENCY_TOTALS


def test_expect_block_overrides_targets_and_aed_is_optional():
    expect = expectations_for({"expect": {
        "rows": 10, "report_rows": 10,
        "currency_totals": {"ils": "1050.00", "KZT": 14940},
    }})
    assert expect["rows"] == 10
    assert expect["currency_totals"] == {"ILS": Decimal("1050.00"), "KZT": Decimal("14940")}
    assert expect["aed_total"] is None
