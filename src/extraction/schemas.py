import datetime as _dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ExtractedReceipt(BaseModel):
    date: _dt.date
    time: _dt.time | None = None

    @field_validator("time", mode="before")
    @classmethod
    def parse_time(cls, v: object) -> object:
        if isinstance(v, str):
            parts = v.split(":")
            return _dt.time(int(parts[0]), int(parts[1]))
        return v

    provider: str | None = None
    category: Literal["taxi", "meals", "hotel", "flight", "other"] = "other"
    amount: Decimal = Field(gt=0)
    currency: str
    from_location: str | None = None
    to_location: str | None = None
    receipt_number: str | None = None
    trip_number: str | None = None
    payment_method: str | None = None
    raw_extracted_text: str = ""
    confidence: float = 0.0


class ExtractionError(BaseModel):
    error: str


class ExtractedTripInfo(BaseModel):
    name: str
    start_date: _dt.date
    end_date: _dt.date


class ReceiptRow(BaseModel):
    id: int
    user_id: str
    date: _dt.date
    time: _dt.time | None = None
    provider: str | None = None
    category: str = "other"
    amount: Decimal
    currency: str
    from_location: str | None = None
    to_location: str | None = None
    receipt_number: str | None = None
    trip_number: str | None = None
    payment_method: str | None = None
    source_kind: str
    source_file_id: str | None = None
    raw_text: str | None = None
    confidence: float | None = None
    status: str = "confirmed"
    notes: str | None = None
