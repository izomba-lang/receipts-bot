from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def confirm_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✓ Confirm", callback_data=f"confirm:{receipt_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{receipt_id}"),
            InlineKeyboardButton("🗑 Discard", callback_data=f"discard:{receipt_id}"),
        ]
    ])


def duplicate_keyboard(
    existing_id: int, new_receipt_data_key: str
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Save anyway", callback_data=f"dup_save:{new_receipt_data_key}"
            ),
            InlineKeyboardButton(
                "Skip", callback_data=f"dup_skip:{new_receipt_data_key}"
            ),
        ]
    ])
