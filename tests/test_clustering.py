from __future__ import annotations

from src.utils.clustering import cluster_receipts, suggest_trip_name


def _r(rid: int, d: str, currency: str = "ILS", category: str = "taxi") -> dict:
    return {"id": rid, "date": d, "currency": currency, "category": category}


def test_empty_returns_empty() -> None:
    assert cluster_receipts([]) == []


def test_single_cluster_when_all_close() -> None:
    receipts = [_r(1, "2026-04-20"), _r(2, "2026-04-21"), _r(3, "2026-04-22")]
    clusters = cluster_receipts(receipts)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_split_on_large_gap() -> None:
    receipts = [
        _r(1, "2026-04-20"),
        _r(2, "2026-04-21"),
        _r(3, "2026-05-15"),
        _r(4, "2026-05-16"),
    ]
    clusters = cluster_receipts(receipts)
    assert len(clusters) == 2
    assert [r["id"] for r in clusters[0]] == [1, 2]
    assert [r["id"] for r in clusters[1]] == [3, 4]


def test_ils_bookends_stay_with_foreign_trip() -> None:
    receipts = [
        _r(1, "2026-04-20", "ILS"),
        _r(2, "2026-04-20", "RUB"),
        _r(3, "2026-04-22", "RUB"),
        _r(4, "2026-04-23", "ILS"),
    ]
    clusters = cluster_receipts(receipts)
    assert len(clusters) == 1, "ILS bookends should not split the cluster"


def test_flight_closes_cluster() -> None:
    receipts = [
        _r(1, "2026-04-20"),
        _r(2, "2026-04-21", category="flight"),
        _r(3, "2026-04-22"),
    ]
    clusters = cluster_receipts(receipts)
    assert len(clusters) == 2
    assert clusters[0][-1]["id"] == 2
    assert clusters[1][0]["id"] == 3


def test_suggest_name_foreign_trip() -> None:
    cluster = [
        _r(1, "2026-04-20", "ILS"),
        _r(2, "2026-04-21", "RUB"),
        _r(3, "2026-04-22", "RUB"),
    ]
    name = suggest_trip_name(cluster)
    assert "RUB" in name
    assert "Apr" in name
    assert "2026" in name


def test_suggest_name_local() -> None:
    cluster = [_r(1, "2026-04-20", "ILS"), _r(2, "2026-04-21", "ILS")]
    name = suggest_trip_name(cluster)
    assert name.startswith("Local")
