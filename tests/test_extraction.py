from __future__ import annotations

import json
from datetime import date, time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.claude_client import ClaudeExtractor
from src.extraction.schemas import ExtractedReceipt, ExtractionError


@pytest.fixture
def extractor() -> ClaudeExtractor:
    return ClaudeExtractor(api_key="test-key", model="claude-sonnet-4-5")


def _mock_response(text: str) -> MagicMock:
    content_block = MagicMock()
    content_block.text = text
    resp = MagicMock()
    resp.content = [content_block]
    return resp


@pytest.mark.asyncio
async def test_extract_from_text_success(extractor: ClaudeExtractor) -> None:
    data = {
        "date": "2026-04-26",
        "time": "14:30",
        "provider": "Yandex Go",
        "category": "taxi",
        "amount": 550.0,
        "currency": "RUB",
        "from_location": "Sheremetyevo",
        "to_location": "Hotel Metropol",
        "receipt_number": None,
        "trip_number": "T-12345",
        "payment_method": "Visa ••1234",
        "raw_extracted_text": "Yandex Go trip 550 RUB",
        "confidence": 0.95,
    }
    mock_resp = _mock_response(json.dumps(data))

    with patch.object(extractor._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await extractor.extract_from_text("Yandex 550 rub", "2026-04-26")

    assert isinstance(result, ExtractedReceipt)
    assert result.provider == "Yandex Go"
    assert result.amount == Decimal("550.0")
    assert result.currency == "RUB"
    assert result.category == "taxi"
    assert result.date == date(2026, 4, 26)
    assert result.time == time(14, 30)


@pytest.mark.asyncio
async def test_extract_not_a_receipt(extractor: ClaudeExtractor) -> None:
    mock_resp = _mock_response(json.dumps({"error": "not_a_receipt"}))

    with patch.object(extractor._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await extractor.extract_from_text("cute cat photo", "2026-04-26")

    assert isinstance(result, ExtractionError)
    assert result.error == "not_a_receipt"


@pytest.mark.asyncio
async def test_extract_with_markdown_wrapper(extractor: ClaudeExtractor) -> None:
    data = {
        "date": "2026-04-20",
        "time": None,
        "provider": "Uber",
        "category": "taxi",
        "amount": 87.0,
        "currency": "AED",
        "from_location": "Airport",
        "to_location": "Hotel",
        "receipt_number": None,
        "trip_number": None,
        "payment_method": None,
        "raw_extracted_text": "Uber 87 AED",
        "confidence": 0.9,
    }
    wrapped = f"```json\n{json.dumps(data)}\n```"
    mock_resp = _mock_response(wrapped)

    with patch.object(extractor._client.messages, "create", new_callable=AsyncMock, return_value=mock_resp):
        result = await extractor.extract_from_text("Uber 87 AED airport→hotel", "2026-04-20")

    assert isinstance(result, ExtractedReceipt)
    assert result.provider == "Uber"
    assert result.amount == Decimal("87.0")
