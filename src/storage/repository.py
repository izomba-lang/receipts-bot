from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from src.extraction.schemas import ExtractedReceipt
from src.storage.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


class ReceiptRepository:
    def __init__(self, client: SupabaseClient, bucket: str) -> None:
        self._db = client
        self._bucket = bucket

    async def save_receipt(
        self,
        user_id: str,
        extracted: ExtractedReceipt,
        source_kind: str,
        file_bytes: bytes | None = None,
        file_ext: str = "jpg",
        file_content_type: str = "image/jpeg",
        status: str = "confirmed",
    ) -> dict[str, Any]:
        trip = await self.find_trip_for_date(user_id, extracted.date)
        row = {
            "user_id": user_id,
            "date": extracted.date.isoformat(),
            "time": extracted.time.isoformat() if extracted.time else None,
            "provider": extracted.provider,
            "category": extracted.category,
            "amount": str(extracted.amount),
            "currency": extracted.currency.upper(),
            "from_location": extracted.from_location,
            "to_location": extracted.to_location,
            "receipt_number": extracted.receipt_number,
            "trip_number": extracted.trip_number,
            "payment_method": extracted.payment_method,
            "source_kind": source_kind,
            "raw_text": extracted.raw_extracted_text or None,
            "confidence": extracted.confidence,
            "status": status,
            "trip_id": trip["id"] if trip else None,
        }
        saved = await self._db.insert("receipts", row)
        if trip:
            saved["_trip_name"] = trip["name"]
        receipt_id = saved["id"]

        if file_bytes:
            path = f"{user_id}/{receipt_id}.{file_ext}"
            storage_path = await self._db.upload_file(
                self._bucket, path, file_bytes, file_content_type
            )
            await self._db.update(
                "receipts", {"id": receipt_id}, {"source_file_id": storage_path}
            )
            saved["source_file_id"] = storage_path

        return saved

    async def replace_receipt(
        self,
        receipt_id: int,
        user_id: str,
        extracted: ExtractedReceipt,
        source_kind: str,
        file_bytes: bytes | None = None,
        file_ext: str = "jpg",
        file_content_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        trip = await self.find_trip_for_date(user_id, extracted.date)
        updates = {
            "date": extracted.date.isoformat(),
            "time": extracted.time.isoformat() if extracted.time else None,
            "provider": extracted.provider,
            "category": extracted.category,
            "amount": str(extracted.amount),
            "currency": extracted.currency.upper(),
            "from_location": extracted.from_location,
            "to_location": extracted.to_location,
            "receipt_number": extracted.receipt_number,
            "trip_number": extracted.trip_number,
            "payment_method": extracted.payment_method,
            "source_kind": source_kind,
            "raw_text": extracted.raw_extracted_text or None,
            "confidence": extracted.confidence,
            "status": "confirmed",
            "trip_id": trip["id"] if trip else None,
        }
        updated = await self.update_receipt(receipt_id, updates)
        if trip:
            updated["_trip_name"] = trip["name"]

        if file_bytes:
            path = f"{user_id}/{receipt_id}.{file_ext}"
            storage_path = await self._db.upload_file(
                self._bucket, path, file_bytes, file_content_type
            )
            await self._db.update(
                "receipts", {"id": receipt_id}, {"source_file_id": storage_path}
            )
            updated["source_file_id"] = storage_path

        return updated

    async def get_receipts(
        self, user_id: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        return await self._db.select(
            "receipts",
            filters={
                "user_id": f"eq.{user_id}",
                "status": "neq.deleted",
                "and": f"(date.gte.{start.isoformat()},date.lte.{end.isoformat()})",
            },
            order="date.asc,time.asc",
        )

    async def get_receipts_in_range(
        self, user_id: str, start: date, end: date,
        include_attachments: bool = False,
    ) -> list[dict[str, Any]]:
        filters = {
            "user_id": f"eq.{user_id}",
            "status": "neq.deleted",
            "and": f"(date.gte.{start.isoformat()},date.lte.{end.isoformat()})",
        }
        if not include_attachments:
            filters["parent_id"] = "is.null"
        resp = await self._db.select(
            "receipts",
            filters=filters,
            order="date.asc,time.asc",
        )
        return resp

    async def find_by_hash(self, user_id: str, sha256: str) -> dict[str, Any] | None:
        rows = await self._db.select(
            "receipts",
            filters={
                "user_id": f"eq.{user_id}",
                "notes": f"like.*sha256:{sha256}*",
                "status": "neq.deleted",
            },
            limit=1,
        )
        return rows[0] if rows else None

    async def update_receipt(
        self, receipt_id: int, updates: dict[str, Any]
    ) -> dict[str, Any]:
        updates["updated_at"] = datetime.utcnow().isoformat()
        return await self._db.update("receipts", {"id": receipt_id}, updates)

    async def soft_delete(self, receipt_id: int) -> dict[str, Any]:
        return await self.update_receipt(
            receipt_id,
            {"status": "deleted", "deleted_at": datetime.utcnow().isoformat()},
        )

    async def restore(self, receipt_id: int) -> dict[str, Any]:
        return await self.update_receipt(
            receipt_id,
            {"status": "confirmed", "deleted_at": None},
        )

    async def get_last_deleted(self, user_id: str) -> dict[str, Any] | None:
        rows = await self._db.select(
            "receipts",
            filters={
                "user_id": f"eq.{user_id}",
                "status": "eq.deleted",
            },
            order="deleted_at.desc",
            limit=1,
        )
        return rows[0] if rows else None

    async def create_trip(
        self, user_id: str, name: str, start: date, end: date, notes: str | None = None
    ) -> dict[str, Any]:
        return await self._db.insert(
            "trips",
            {
                "user_id": user_id,
                "name": name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "notes": notes,
            },
        )

    async def list_trips(self, user_id: str) -> list[dict[str, Any]]:
        return await self._db.select(
            "trips",
            filters={"user_id": f"eq.{user_id}"},
            order="start_date.desc",
        )

    async def find_trip_for_date(
        self, user_id: str, on_date: date
    ) -> dict[str, Any] | None:
        rows = await self._db.select(
            "trips",
            filters={
                "user_id": f"eq.{user_id}",
                "and": (
                    f"(start_date.lte.{on_date.isoformat()},"
                    f"end_date.gte.{on_date.isoformat()})"
                ),
            },
            order="start_date.desc",
            limit=1,
        )
        return rows[0] if rows else None

    async def find_trip_by_name(
        self, user_id: str, query: str
    ) -> dict[str, Any] | None:
        rows = await self._db.select(
            "trips",
            filters={
                "user_id": f"eq.{user_id}",
                "name": f"ilike.*{query}*",
            },
            order="start_date.desc",
            limit=1,
        )
        return rows[0] if rows else None

    async def get_trip(self, user_id: str, trip_id: int) -> dict[str, Any] | None:
        rows = await self._db.select(
            "trips",
            filters={"user_id": f"eq.{user_id}", "id": f"eq.{trip_id}"},
            limit=1,
        )
        return rows[0] if rows else None

    async def get_receipts_by_trip(
        self, user_id: str, trip_id: int,
        include_attachments: bool = False,
    ) -> list[dict[str, Any]]:
        filters = {
            "user_id": f"eq.{user_id}",
            "trip_id": f"eq.{trip_id}",
            "status": "neq.deleted",
        }
        if not include_attachments:
            filters["parent_id"] = "is.null"
        return await self._db.select(
            "receipts",
            filters=filters,
            order="date.asc,time.asc",
        )

    async def assign_trip(self, receipt_id: int, trip_id: int | None) -> dict[str, Any]:
        return await self.update_receipt(receipt_id, {"trip_id": trip_id})

    async def get_unassigned_receipts(self, user_id: str) -> list[dict[str, Any]]:
        return await self._db.select(
            "receipts",
            filters={
                "user_id": f"eq.{user_id}",
                "trip_id": "is.null",
                "status": "neq.deleted",
                "parent_id": "is.null",
            },
            order="date.asc,time.asc",
        )

    async def get_receipt(self, user_id: str, receipt_id: int) -> dict[str, Any] | None:
        rows = await self._db.select(
            "receipts",
            filters={"user_id": f"eq.{user_id}", "id": f"eq.{receipt_id}"},
            limit=1,
        )
        return rows[0] if rows else None

    async def find_merge_candidate(
        self, user_id: str, on_date: date, new_receipt: dict[str, Any],
        within_minutes: int = 5,
    ) -> dict[str, Any] | None:
        """Find a recently-captured receipt on the same date that the new one
        likely belongs to (same payment: bill + fiscal receipt)."""
        created_raw = new_receipt.get("created_at")
        if not created_raw:
            return None
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        cutoff = (created - timedelta(minutes=within_minutes)).isoformat()

        rows = await self._db.select(
            "receipts",
            filters={
                "user_id": f"eq.{user_id}",
                "date": f"eq.{on_date.isoformat()}",
                "status": "neq.deleted",
                "parent_id": "is.null",
                "id": f"neq.{new_receipt['id']}",
                "created_at": f"gte.{cutoff}",
            },
            order="created_at.desc",
            limit=1,
        )
        return rows[0] if rows else None

    async def merge_receipts(
        self, primary_id: int, attachment_id: int
    ) -> None:
        await self.update_receipt(attachment_id, {"parent_id": primary_id})

    async def unmerge_receipt(self, receipt_id: int) -> dict[str, Any]:
        return await self.update_receipt(receipt_id, {"parent_id": None})
