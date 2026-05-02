from __future__ import annotations

import re
from datetime import date, timedelta


def parse_report_range(arg: str | None) -> tuple[date, date]:
    if not arg or not arg.strip():
        today = date.today()
        return _month_range(today.year, today.month)

    arg = arg.strip()

    if ".." in arg:
        parts = arg.split("..", 1)
        return date.fromisoformat(parts[0]), date.fromisoformat(parts[1])

    m = re.fullmatch(r"(\d{4})-(\d{2})", arg)
    if m:
        return _month_range(int(m.group(1)), int(m.group(2)))

    raise ValueError(f"Invalid range: {arg!r}. Use YYYY-MM or YYYY-MM-DD..YYYY-MM-DD")


def _month_range(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return first, last


def resolve_relative_date(text: str, anchor: date) -> date | None:
    low = text.lower().strip()
    if low == "today":
        return anchor
    if low == "yesterday":
        return anchor - timedelta(days=1)
    return None
