from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.report.fx import FxRateProvider


@pytest.fixture
def fx() -> FxRateProvider:
    return FxRateProvider()


@pytest.mark.asyncio
async def test_aed_to_aed_is_one(fx: FxRateProvider) -> None:
    rate = await fx.get_rate(date(2026, 4, 26), "AED")
    assert rate == Decimal("1")


@pytest.mark.asyncio
async def test_fetch_rate_success(fx: FxRateProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"rub": {"aed": 0.04123}}

    with patch.object(fx._http, "get", new_callable=AsyncMock, return_value=mock_resp):
        rate = await fx.get_rate(date(2026, 4, 26), "RUB")

    assert rate == Decimal("0.04123")


@pytest.mark.asyncio
async def test_rate_cached(fx: FxRateProvider) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ils": {"aed": 1.01234}}

    with patch.object(fx._http, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        r1 = await fx.get_rate(date(2026, 4, 26), "ILS")
        r2 = await fx.get_rate(date(2026, 4, 26), "ILS")

    assert r1 == r2
    assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_fallback_to_previous_day(fx: FxRateProvider) -> None:
    fail_resp = MagicMock()
    fail_resp.status_code = 404

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"eur": {"aed": 4.0}}

    with patch.object(fx._http, "get", new_callable=AsyncMock, side_effect=[fail_resp, ok_resp]):
        rate = await fx.get_rate(date(2026, 4, 27), "EUR")

    assert rate == Decimal("4.0")


@pytest.mark.asyncio
async def test_all_days_fail_raises(fx: FxRateProvider) -> None:
    fail_resp = MagicMock()
    fail_resp.status_code = 404

    with patch.object(fx._http, "get", new_callable=AsyncMock, return_value=fail_resp):
        with pytest.raises(RuntimeError, match="Could not fetch FX rate"):
            await fx.get_rate(date(2026, 4, 26), "XYZ")
