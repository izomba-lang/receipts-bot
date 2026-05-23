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
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{receipt_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"del:{receipt_id}"),
        ]
    ])


# Fields offered in the edit dialog: (label, field_key)
_EDIT_FIELDS = [
    ("Amount", "amount"),
    ("Currency", "currency"),
    ("Category", "category"),
    ("Provider", "provider"),
    ("Date", "date"),
    ("From", "from_location"),
    ("To", "to_location"),
    ("Payment", "payment_method"),
]

CATEGORY_VALUES = ["taxi", "meals", "hotel", "flight", "other"]


def edit_field_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for label, field in _EDIT_FIELDS:
        row.append(
            InlineKeyboardButton(label, callback_data=f"editf:{receipt_id}:{field}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✖️ Cancel", callback_data=f"edit_cancel:{receipt_id}")])
    return InlineKeyboardMarkup(rows)


def edit_category_keyboard(receipt_id: int) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for val in CATEGORY_VALUES:
        row.append(
            InlineKeyboardButton(
                val.capitalize(), callback_data=f"editv:{receipt_id}:category:{val}"
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


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
