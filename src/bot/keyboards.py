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


def duplicate_keyboard(sha: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏭ Skip", callback_data=f"dup_skip:{sha}"),
            InlineKeyboardButton("🔄 Replace", callback_data=f"dup_replace:{sha}"),
            InlineKeyboardButton("➕ Save as new", callback_data=f"dup_new:{sha}"),
        ]
    ])


def saved_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete", callback_data=f"del:{receipt_id}")]
    ])


def merge_keyboard(new_id: int, candidate_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔗 Merge (same payment)",
                callback_data=f"merge:{new_id}:{candidate_id}",
            ),
            InlineKeyboardButton(
                "❌ Separate", callback_data=f"merge_no:{new_id}"
            ),
        ]
    ])
