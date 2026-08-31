from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.pyrus.com/api/v4/auth"

# Form + catalog item IDs for "Payment. UAE" (form 1135007), captured from a
# real reimbursement ticket. These are Dodo-internal but not secret — they are
# the same for everyone submitting this form. Personal values (counterparty name,
# the assistant to notify) are passed in from config / env, not hardcoded.
FORM_ID = 1135007
COMPANY_ITEM_ID = 103661856          # DODO BRANDS INTERNATIONAL FZCO
CURRENCY_AED_ITEM_ID = 96970640      # AED (catalog rate ~24)
DEPARTMENT_ITEM_ID = 174735390       # Dodo Pizza.IMF.Platform
MARKET_ITEM_ID = 175349073           # Dodo Pizza.International Region (w/o MENA)
EXPENSE_TYPE_ITEM_ID = 79174514      # Business trips_Other / Командировки_Прочее
TYPE_REIMBURSEMENT_CHOICE = 3
AED_TO_RUB_RATE = 24                 # rate stored in the Pyrus currency catalog


class PyrusClient:
    def __init__(self, login: str, security_key: str) -> None:
        self._login = login
        self._security_key = security_key
        self._http = httpx.AsyncClient(timeout=60.0)
        self._token: str | None = None
        self._api_url = "https://api.pyrus.com/v4/"
        self._files_url = "https://files.pyrus.com/"

    async def _auth(self) -> None:
        resp = await self._http.post(
            AUTH_URL,
            json={"login": self._login, "security_key": self._security_key},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._api_url = data.get("api_url", self._api_url)
        self._files_url = data.get("files_url", self._files_url)

    async def _headers(self) -> dict[str, str]:
        if not self._token:
            await self._auth()
        return {"Authorization": f"Bearer {self._token}"}

    async def upload_file(self, filename: str, data: bytes) -> str:
        # Upload endpoint lives under the API base, not the files (download) host.
        url = f"{self._api_url}files/upload"
        headers = await self._headers()
        files = {"file": (filename, data)}
        resp = await self._http.post(url, headers=headers, files=files)
        if resp.status_code == 401:
            await self._auth()
            headers = await self._headers()
            resp = await self._http.post(url, headers=headers, files=files)
        if resp.status_code >= 400:
            logger.error("Pyrus upload failed %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        return resp.json()["guid"]

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self._http.post(
            f"{self._api_url}tasks", headers=headers, json=payload
        )
        if resp.status_code == 401:
            await self._auth()
            headers = await self._headers()
            resp = await self._http.post(
                f"{self._api_url}tasks", headers=headers, json=payload
            )
        if resp.status_code >= 400:
            logger.error("Pyrus create_task failed %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()


def build_payment_payload(
    purpose: str,
    aed_total: float,
    payment_date: str,
    counterparty_name: str,
    receipt_guids: list[str] | None = None,
    assistant_person_id: int | None = None,
) -> dict[str, Any]:
    """Build the create-task payload for the Payment.UAE reimbursement form.
    Bank details are intentionally left empty (filled downstream, as in the
    real process)."""
    rub_amount = round(aed_total * AED_TO_RUB_RATE, 2)

    type_field: dict[str, Any] = {"choice_id": TYPE_REIMBURSEMENT_CHOICE}
    if receipt_guids:
        type_field["fields"] = [
            {"id": 10, "value": [{"guid": g} for g in receipt_guids]}
        ]

    fields: list[dict[str, Any]] = [
        {"id": 51, "value": {"item_id": COMPANY_ITEM_ID}},
        {"id": 1, "value": purpose},
        {"id": 34, "value": counterparty_name},
        {"id": 4, "value": round(aed_total, 2)},
        {"id": 31, "value": {"item_id": CURRENCY_AED_ITEM_ID}},
        {"id": 64, "value": rub_amount},
        {"id": 5, "value": 0},
        {"id": 6, "value": type_field},
        {"id": 50, "value": {"item_id": DEPARTMENT_ITEM_ID}},
        {"id": 71, "value": {"item_id": MARKET_ITEM_ID}},
        {"id": 12, "value": {"item_id": EXPENSE_TYPE_ITEM_ID}},
        {"id": 13, "value": "Командировки_Прочее"},
        {"id": 20, "value": payment_date},
    ]
    payload: dict[str, Any] = {"form_id": FORM_ID, "fields": fields}
    if assistant_person_id:
        payload["subscribers"] = [{"id": assistant_person_id}]
    return payload
