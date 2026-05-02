from __future__ import annotations

import calendar
from collections import Counter
from datetime import date
from typing import Any

GAP_DAYS = 2
HOME_CURRENCY = "ILS"


def cluster_receipts(
    receipts: list[dict[str, Any]],
    gap_days: int = GAP_DAYS,
) -> list[list[dict[str, Any]]]:
    if not receipts:
        return []

    sorted_r = sorted(receipts, key=lambda r: (r["date"], r.get("time") or ""))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = [sorted_r[0]]

    for r in sorted_r[1:]:
        prev = current[-1]
        prev_date = _to_date(prev["date"])
        cur_date = _to_date(r["date"])
        gap = (cur_date - prev_date).days
        prev_was_flight = (prev.get("category") or "") == "flight"

        if gap > gap_days or prev_was_flight:
            clusters.append(current)
            current = [r]
        else:
            current.append(r)

    clusters.append(current)
    return clusters


def suggest_trip_name(
    cluster: list[dict[str, Any]], home_currency: str = HOME_CURRENCY
) -> str:
    foreign = [
        (r.get("currency") or "").upper()
        for r in cluster
        if (r.get("currency") or "").upper() != home_currency
    ]
    first_date = _to_date(cluster[0]["date"])
    month = calendar.month_abbr[first_date.month]
    year = first_date.year

    if foreign:
        dominant = Counter(foreign).most_common(1)[0][0]
        return f"Trip {dominant} {month} {year}"
    return f"Local {month} {year}"


def _to_date(v: object) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v))
