from __future__ import annotations

from datetime import date

import pytest

from src.utils.dates import parse_report_range, resolve_relative_date


def test_parse_month() -> None:
    start, end = parse_report_range("2026-04")
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 30)


def test_parse_range() -> None:
    start, end = parse_report_range("2026-04-10..2026-04-20")
    assert start == date(2026, 4, 10)
    assert end == date(2026, 4, 20)


def test_parse_none_uses_current_month() -> None:
    start, end = parse_report_range(None)
    today = date.today()
    assert start.year == today.year
    assert start.month == today.month
    assert start.day == 1


def test_parse_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_report_range("garbage")


def test_parse_december() -> None:
    start, end = parse_report_range("2026-12")
    assert start == date(2026, 12, 1)
    assert end == date(2026, 12, 31)


def test_resolve_today() -> None:
    anchor = date(2026, 4, 29)
    assert resolve_relative_date("today", anchor) == anchor


def test_resolve_yesterday() -> None:
    anchor = date(2026, 4, 29)
    assert resolve_relative_date("yesterday", anchor) == date(2026, 4, 28)


def test_resolve_unknown_returns_none() -> None:
    assert resolve_relative_date("last week", date(2026, 4, 29)) is None
