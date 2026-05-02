from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SupabaseClient:
    def __init__(self, url: str, service_key: str) -> None:
        self._base = url.rstrip("/")
        self._headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self._http = httpx.AsyncClient(timeout=30.0)

    @property
    def rest_url(self) -> str:
        return f"{self._base}/rest/v1"

    @property
    def storage_url(self) -> str:
        return f"{self._base}/storage/v1"

    async def insert(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self.rest_url}/{table}",
            headers=self._headers,
            json=data,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) else rows

    async def update(
        self, table: str, filters: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        params = {f"{k}": f"eq.{v}" for k, v in filters.items()}
        resp = await self._http.patch(
            f"{self.rest_url}/{table}",
            headers=self._headers,
            params=params,
            json=data,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if isinstance(rows, list) and rows else {}

    async def select(
        self,
        table: str,
        *,
        filters: dict[str, str] | None = None,
        order: str = "date.asc,time.asc",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if filters:
            params.update(filters)
        if order:
            params["order"] = order
        if limit:
            params["limit"] = str(limit)
        resp = await self._http.get(
            f"{self.rest_url}/{table}",
            headers=self._headers,
            params=params,
        )
        resp.raise_for_status()
        return resp.json()

    async def upload_file(
        self, bucket: str, path: str, data: bytes, content_type: str
    ) -> str:
        headers = {
            "apikey": self._headers["apikey"],
            "Authorization": self._headers["Authorization"],
            "Content-Type": content_type,
        }
        resp = await self._http.post(
            f"{self.storage_url}/object/{bucket}/{path}",
            headers=headers,
            content=data,
        )
        if resp.status_code == 400 and "already exists" in resp.text.lower():
            resp = await self._http.put(
                f"{self.storage_url}/object/{bucket}/{path}",
                headers=headers,
                content=data,
            )
        resp.raise_for_status()
        return f"{bucket}/{path}"

    async def close(self) -> None:
        await self._http.aclose()
