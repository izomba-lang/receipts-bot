from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

API_TEMPLATE = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api"
    "@{date}/v1/currencies/{base}.min.json"
)


class FxRateProvider:
    def __init__(self) -> None:
        self._cache: dict[tuple[date, str], Decimal] = {}
        self._http = httpx.AsyncClient(timeout=15.0)

    async def get_rate(self, on_date: date, from_currency: str) -> Decimal:
        from_curr = from_currency.lower()
        if from_curr == "aed":
            return Decimal("1")

        key = (on_date, from_curr)
        if key in self._cache:
            return self._cache[key]

        rate = await self._fetch_rate(on_date, from_curr)
        self._cache[key] = rate
        return rate

    async def _fetch_rate(self, on_date: date, from_curr: str) -> Decimal:
        for offset in range(8):
            d = on_date - timedelta(days=offset)
            url = API_TEMPLATE.format(date=d.isoformat(), base=from_curr)
            try:
                resp = await self._http.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    rates = data.get(from_curr, {})
                    aed_rate = rates.get("aed")
                    if aed_rate is not None:
                        rate = Decimal(str(aed_rate))
                        logger.info(
                            "FX %s→AED on %s: %s (data from %s)",
                            from_curr.upper(), on_date, rate, d,
                        )
                        return rate
            except httpx.HTTPError:
                logger.warning("FX API error for %s on %s", from_curr, d)
                continue

        raise RuntimeError(
            f"Could not fetch FX rate for {from_curr.upper()}→AED "
            f"on {on_date} (tried 8 days back)"
        )

    async def close(self) -> None:
        await self._http.aclose()
