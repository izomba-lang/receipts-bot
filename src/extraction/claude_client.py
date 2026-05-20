from __future__ import annotations

import base64
import json
import logging
from typing import Any

import anthropic

from src.extraction.prompts import RECEIPT_EXTRACTION_SYSTEM, build_text_extraction_prompt
from src.extraction.schemas import ExtractedReceipt, ExtractionError

logger = logging.getLogger(__name__)


class ClaudeExtractor:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def extract_from_image(
        self, image_bytes: bytes, media_type: str, anchor_date: str
    ) -> ExtractedReceipt | ExtractionError:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {
                "type": "text",
                "text": f"Extract receipt data. Today's date for reference: {anchor_date}",
            },
        ]
        return await self._call(content)

    async def extract_from_pdf(
        self, pdf_bytes: bytes, anchor_date: str
    ) -> ExtractedReceipt | ExtractionError:
        b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        content: list[dict[str, Any]] = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {
                "type": "text",
                "text": f"Extract receipt data. Today's date for reference: {anchor_date}",
            },
        ]
        return await self._call(content)

    async def extract_from_text(
        self, text: str, anchor_date: str
    ) -> ExtractedReceipt | ExtractionError:
        prompt = build_text_extraction_prompt(text, anchor_date)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        return await self._call(content)

    async def _call(
        self, content: list[dict[str, Any]]
    ) -> ExtractedReceipt | ExtractionError:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=RECEIPT_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        logger.debug("Claude raw response (stop=%s): %s", response.stop_reason, raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(
                "Claude returned invalid JSON (stop_reason=%s, len=%d): %s | error: %s",
                response.stop_reason, len(raw), raw[:500], e,
            )
            return ExtractionError(
                error=f"claude_invalid_json (stop_reason={response.stop_reason})"
            )

        if "error" in data:
            return ExtractionError(**data)

        try:
            return ExtractedReceipt(**data)
        except Exception as e:
            logger.error("Failed to parse ExtractedReceipt: %s | data: %s", e, data)
            return ExtractionError(error=f"schema_mismatch: {e}")
