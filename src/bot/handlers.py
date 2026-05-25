from __future__ import annotations

import logging
from datetime import date
from typing import Any

from telegram import PhotoSize, Update
from telegram.ext import ContextTypes

from src.bot.keyboards import (
    confirm_keyboard,
    duplicate_keyboard,
    edit_category_keyboard,
    edit_field_keyboard,
    merge_keyboard,
    saved_keyboard,
)
from src.extraction.claude_client import ClaudeExtractor
from src.extraction.schemas import ExtractedReceipt, ExtractionError
from src.storage.repository import ReceiptRepository
from src.utils.dedup import sha256_digest

FIELD_LABELS = {
    "amount": "amount (e.g. 600 or 550.50)",
    "currency": "currency (ISO code, e.g. ILS, GEL, AMD, EUR)",
    "provider": "provider name",
    "date": "date (YYYY-MM-DD)",
    "from_location": "from location",
    "to_location": "to location",
    "payment_method": "payment method",
}

logger = logging.getLogger(__name__)

CURRENCY_SYMBOLS: dict[str, str] = {
    "RUB": "₽", "ILS": "₪", "AED": "د.إ", "USD": "$", "EUR": "€",
    "GBP": "£", "TRY": "₺",
}


def _format_confirmation(saved: dict[str, Any]) -> str:
    rid = saved["id"]
    provider = saved.get("provider") or "Unknown"
    d = saved.get("date", "")
    amount = saved.get("amount", 0)
    currency = saved.get("currency", "")
    category = (saved.get("category") or "other").capitalize()
    sym = CURRENCY_SYMBOLS.get(currency, "")
    amount_fmt = f"{sym}{float(amount):,.2f}" if sym else f"{float(amount):,.2f} {currency}"
    trip_name = saved.get("_trip_name")
    trip_line = f"\n   ✈️ Trip: {trip_name}" if trip_name else ""
    return (
        f"✅ Saved #{rid} — {provider} · {d} · {amount_fmt} · {category}"
        f"{trip_line}\n"
        f"   To fix, type: /edit {rid} amount=… (or use 🗑 below to remove)"
    )


def owner_only(func):  # type: ignore[no-untyped-def]
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        owner_id = context.bot_data.get("owner_id")
        user = update.effective_user
        if not user or user.id != owner_id:
            if update.message:
                await update.message.reply_text(
                    "Sorry, this is a private bot. Access denied."
                )
            return
        return await func(update, context)
    return wrapper


@owner_only
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text(
        "👋 Receipts Bot ready!\n\n"
        "Send me receipt photos, PDFs, or text descriptions of expenses.\n"
        "I'll extract the data and store it.\n\n"
        "Commands:\n"
        "/report YYYY-MM — generate expense report\n"
        "/list YYYY-MM — list receipts\n"
        "/edit <id> <field>=<value> — edit a receipt\n"
        "/delete <id> — delete a receipt\n"
        "/undo — restore last deleted\n"
        "/help — full command reference"
    )


@owner_only
async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    await update.message.reply_text(
        "📋 Command reference:\n\n"
        "/report — report for current month\n"
        "/report YYYY-MM — report for a specific month\n"
        "/report YYYY-MM-DD..YYYY-MM-DD — report for date range\n"
        "/report trip:<name|id> — report for a trip\n"
        "/list YYYY-MM — list receipts for a month\n"
        "/list trip:<name|id> — list receipts for a trip\n\n"
        "✈️ Trips:\n"
        "/trip new \"Name\" YYYY-MM-DD..YYYY-MM-DD — create trip\n"
        "/trip list — list trips\n"
        "/trip current — show active trip for today\n"
        "/trip assign <receipt_id> <trip_id|name> — manual attach\n"
        "/trip unassign <receipt_id> — detach\n\n"
        "/edit <id> <field>=<value> — edit receipt field\n"
        "  Fields: amount, currency, category, provider, date, "
        "from_location, to_location, payment_method, notes\n"
        "/delete <id> — soft-delete a receipt\n"
        "/undo — restore the last deleted receipt\n"
        "/cancel — reset any pending state\n\n"
        "Receipts auto-attach to a trip if their date falls in the trip's range."
    )


@owner_only
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    photos: tuple[PhotoSize, ...] = update.message.photo or ()
    if not photos:
        return

    best = max(photos, key=lambda p: p.file_size or 0)
    file = await best.get_file()
    file_bytes = await file.download_as_bytearray()
    image_bytes = bytes(file_bytes)

    sha = sha256_digest(image_bytes)
    existing = await repo.find_by_hash(user_id, sha)
    if existing:
        context.user_data[f"dup_{sha}"] = {  # type: ignore[index]
            "file_bytes": image_bytes,
            "source_kind": "photo",
            "ext": "jpg",
            "content_type": "image/jpeg",
            "existing_id": existing["id"],
        }
        await update.message.reply_text(
            f"⚠️ Duplicate of #{existing['id']} "
            f"({existing.get('provider') or '—'} · {existing.get('date')}).\n"
            f"What do you want to do?",
            reply_markup=duplicate_keyboard(sha),
        )
        return

    await _process_image(update, context, image_bytes, sha, "photo")


@owner_only
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message and update.message.document
    doc = update.message.document
    mime = doc.mime_type or ""

    if not (
        mime.startswith("image/")
        or mime == "application/pdf"
    ):
        await update.message.reply_text(
            "I can process images and PDFs only. Please send a receipt photo or PDF."
        )
        return

    if (doc.file_size or 0) > 20 * 1024 * 1024:
        await update.message.reply_text(
            "File is too large (>20 MB). Please compress and resend."
        )
        return

    extractor: ClaudeExtractor = context.bot_data["extractor"]
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    file = await doc.get_file()
    file_bytes = bytes(await file.download_as_bytearray())

    if mime == "application/pdf":
        source_kind = "pdf"
        ext = "pdf"
        content_type = "application/pdf"
    else:
        media_type = mime if "/" in mime else "image/jpeg"
        source_kind = "document"
        ext = mime.split("/")[-1] if "/" in mime else "jpg"
        content_type = mime

    sha = sha256_digest(file_bytes)
    existing = await repo.find_by_hash(user_id, sha)
    if existing:
        context.user_data[f"dup_{sha}"] = {  # type: ignore[index]
            "file_bytes": file_bytes,
            "source_kind": source_kind,
            "ext": ext,
            "content_type": content_type,
            "existing_id": existing["id"],
        }
        await update.message.reply_text(
            f"⚠️ Duplicate of #{existing['id']} "
            f"({existing.get('provider') or '—'} · {existing.get('date')}).\n"
            f"What do you want to do?",
            reply_markup=duplicate_keyboard(sha),
        )
        return

    anchor = date.today().isoformat()

    if mime == "application/pdf":
        result = await extractor.extract_from_pdf(file_bytes, anchor)
    else:
        result = await extractor.extract_from_image(file_bytes, media_type, anchor)

    if isinstance(result, ExtractionError):
        await update.message.reply_text(
            "🤷 This doesn't look like a receipt. "
            "If it is, try sending a clearer photo."
        )
        return

    await _save_and_reply(
        update, repo, user_id, result, source_kind,
        file_bytes, ext, content_type, sha,
    )


@owner_only
async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    assert update.message and update.message.text
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    repo: ReceiptRepository = context.bot_data["repo"]

    # If an edit dialog is awaiting a value, consume this message as that value.
    pending = context.user_data.get("pending_edit")  # type: ignore[union-attr]
    if pending:
        await _apply_pending_edit(update, repo, pending, text)
        context.user_data.pop("pending_edit", None)  # type: ignore[union-attr]
        return

    extractor: ClaudeExtractor = context.bot_data["extractor"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]
    anchor = date.today().isoformat()

    result = await extractor.extract_from_text(text, anchor)

    if isinstance(result, ExtractionError):
        await update.message.reply_text(
            "I couldn't parse that as an expense. "
            "Try something like: \"Uber 87 AED yesterday airport→hotel\""
        )
        return

    if result.date > date.today():
        await update.message.reply_text("❌ Date is in the future — please check and resend.")
        return

    if result.confidence < 0.7:
        context.user_data["pending_receipt"] = result  # type: ignore[index]
        await update.message.reply_text(
            f"🔍 I parsed this but I'm not confident:\n\n"
            f"Provider: {result.provider or '—'}\n"
            f"Date: {result.date}\n"
            f"Amount: {result.amount} {result.currency}\n"
            f"Category: {result.category}\n\n"
            f"Save as pending — you can fix with /edit later.",
        )
        saved = await repo.save_receipt(
            user_id, result, "text", status="pending_review"
        )
        await update.message.reply_text(
            _format_confirmation(saved), reply_markup=saved_keyboard(saved["id"])
        )
        return

    saved = await repo.save_receipt(user_id, result, "text")
    await update.message.reply_text(
        _format_confirmation(saved), reply_markup=saved_keyboard(saved["id"])
    )


async def handle_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    repo: ReceiptRepository = context.bot_data["repo"]
    data = query.data

    if data.startswith("confirm:"):
        receipt_id = int(data.split(":")[1])
        await repo.update_receipt(receipt_id, {"status": "confirmed"})
        await query.edit_message_text(f"✅ Receipt #{receipt_id} confirmed.")

    elif data.startswith("discard:"):
        receipt_id = int(data.split(":")[1])
        await repo.soft_delete(receipt_id)
        await query.edit_message_text(f"🗑 Receipt #{receipt_id} discarded.")

    elif data.startswith("del:"):
        receipt_id = int(data.split(":")[1])
        await repo.soft_delete(receipt_id)
        context.user_data["last_deleted"] = receipt_id  # type: ignore[index]
        await query.edit_message_text(
            f"🗑 Receipt #{receipt_id} deleted. Send /undo to restore."
        )

    elif data.startswith("edit:"):
        receipt_id = int(data.split(":")[1])
        await query.edit_message_text(
            f"✏️ Editing receipt #{receipt_id}. Which field?",
            reply_markup=edit_field_keyboard(receipt_id),
        )

    elif data.startswith("edit_cancel:"):
        context.user_data.pop("pending_edit", None)  # type: ignore[union-attr]
        await query.edit_message_text("Edit cancelled.")

    elif data.startswith("editf:"):
        _, rid_s, field = data.split(":", 2)
        receipt_id = int(rid_s)
        if field == "category":
            await query.edit_message_text(
                f"Pick a category for #{receipt_id}:",
                reply_markup=edit_category_keyboard(receipt_id),
            )
        else:
            context.user_data["pending_edit"] = {  # type: ignore[index]
                "id": receipt_id, "field": field
            }
            await query.edit_message_text(
                f"✏️ Send the new {FIELD_LABELS.get(field, field)} for #{receipt_id}.\n"
                f"(or /cancel)"
            )

    elif data.startswith("editv:"):
        _, rid_s, field, value = data.split(":", 3)
        receipt_id = int(rid_s)
        await repo.update_receipt(receipt_id, {field: value})
        await query.edit_message_text(
            f"✅ #{receipt_id}: {field} → {value}"
        )

    elif data.startswith("close:"):
        from src.bot.commands import build_forwardable_summary, run_trip_export

        trip_id = int(data.split(":")[1])
        user_id = str(update.effective_user.id)  # type: ignore[union-attr]
        trip = await repo.get_trip(user_id, trip_id)
        if not trip:
            await query.edit_message_text("Trip not found.")
            return
        if trip.get("closed_at"):
            await query.edit_message_text(f"Trip \"{trip['name']}\" is already closed.")
            return
        sb = context.bot_data["sb_client"]
        await query.edit_message_text(
            f"🏁 Closing \"{trip['name']}\" and generating the final report…"
        )
        try:
            res = await run_trip_export(repo, sb, user_id, trip)
        except ValueError as e:
            await query.edit_message_text(f"❌ {e}")
            return
        await repo.close_trip(trip_id)
        if query.message:
            await query.message.reply_text(f"🏁 Trip \"{trip['name']}\" closed.")
            await query.message.reply_text(
                build_forwardable_summary(trip, res),
                disable_web_page_preview=True,
            )

    elif data.startswith("dup_skip:"):
        sha = data.split(":", 1)[1]
        context.user_data.pop(f"dup_{sha}", None)  # type: ignore[union-attr]
        await query.edit_message_text("⏭ Skipped — nothing saved.")

    elif data.startswith("dup_new:") or data.startswith("dup_replace:"):
        action, sha = data.split(":", 1)
        pending = context.user_data.pop(f"dup_{sha}", None)  # type: ignore[union-attr]
        if not pending:
            await query.edit_message_text("Session expired. Resend the file.")
            return

        extractor: ClaudeExtractor = context.bot_data["extractor"]
        user_id = str(update.effective_user.id)  # type: ignore[union-attr]
        anchor = date.today().isoformat()

        await query.edit_message_text("⏳ Extracting…")
        result = await _extract_pending(extractor, pending, anchor)

        if isinstance(result, ExtractionError):
            await query.edit_message_text(f"⚠️ Could not parse: {result.error}")
            return
        if result.date > date.today():
            await query.edit_message_text("❌ Date is in the future — skipped.")
            return

        if action == "dup_replace":
            saved = await repo.replace_receipt(
                pending["existing_id"], user_id, result, pending["source_kind"],
                file_bytes=pending["file_bytes"],
                file_ext=pending["ext"],
                file_content_type=pending["content_type"],
            )
            await query.edit_message_text(
                f"🔄 Replaced #{pending['existing_id']}.\n\n"
                + _format_confirmation({**saved, "id": pending["existing_id"]}).replace(
                    "✅ Saved", "🔄 Updated"
                )
            )
        else:  # dup_new
            saved = await repo.save_receipt(
                user_id, result, pending["source_kind"],
                file_bytes=pending["file_bytes"],
                file_ext=pending["ext"],
                file_content_type=pending["content_type"],
                status="confirmed" if result.confidence >= 0.7 else "pending_review",
            )
            await repo.update_receipt(saved["id"], {"notes": f"sha256:{sha}"})
            await query.edit_message_text(
                f"➕ Saved as new entry.\n\n{_format_confirmation(saved)}"
            )

    elif data.startswith("trip_create_all:"):
        proposal_id = data.split(":", 1)[1]
        key = f"trip_proposal_{proposal_id}"
        proposals = context.user_data.get(key)  # type: ignore[union-attr]
        if not proposals:
            await query.edit_message_text("Proposal expired. Run /trip suggest again.")
            return
        user_id = str(update.effective_user.id)  # type: ignore[union-attr]
        created: list[str] = []
        for p in proposals:
            trip = await repo.create_trip(
                user_id,
                p["name"],
                date.fromisoformat(p["start"]),
                date.fromisoformat(p["end"]),
            )
            for rid in p["receipt_ids"]:
                await repo.assign_trip(rid, trip["id"])
            created.append(f"#{trip['id']} \"{trip['name']}\"")
        context.user_data.pop(key, None)  # type: ignore[union-attr]
        await query.edit_message_text(
            f"✅ Created {len(created)} trip(s):\n" + "\n".join(f"• {c}" for c in created)
        )

    elif data.startswith("trip_cancel:"):
        proposal_id = data.split(":", 1)[1]
        context.user_data.pop(f"trip_proposal_{proposal_id}", None)  # type: ignore[union-attr]
        await query.edit_message_text("Cancelled.")

    elif data.startswith("merge:"):
        _, new_id_s, cand_id_s = data.split(":")
        user_id = str(update.effective_user.id)  # type: ignore[union-attr]
        new_r = await repo.get_receipt(user_id, int(new_id_s))
        cand_r = await repo.get_receipt(user_id, int(cand_id_s))
        if not new_r or not cand_r:
            await query.edit_message_text("One of the receipts no longer exists.")
            return
        # Larger amount stays as the counted (primary); the other is the attachment
        if float(new_r["amount"]) >= float(cand_r["amount"]):
            primary, attachment = new_r, cand_r
        else:
            primary, attachment = cand_r, new_r
        await repo.merge_receipts(primary["id"], attachment["id"])
        await query.edit_message_text(
            f"🔗 Merged. #{primary['id']} "
            f"({float(primary['amount']):,.2f} {primary['currency']}) is counted; "
            f"#{attachment['id']} is now a supporting document.\n"
            f"Undo with /unmerge {attachment['id']}"
        )

    elif data.startswith("merge_no:"):
        await query.edit_message_text("Kept as separate receipts.")

    elif data.startswith("pyrus_no:"):
        token = data.split(":", 1)[1]
        context.user_data.pop(f"pyrus_{token}", None)  # type: ignore[union-attr]
        await query.edit_message_text("Pyrus ticket cancelled.")

    elif data.startswith("pyrus_go:"):
        from src.bot.commands import create_pyrus_ticket

        token = data.split(":", 1)[1]
        payload_data = context.user_data.pop(f"pyrus_{token}", None)  # type: ignore[union-attr]
        if not payload_data:
            await query.edit_message_text("Preview expired. Run /pyrus again.")
            return
        await query.edit_message_text("📨 Creating Pyrus ticket…")
        user_id = str(update.effective_user.id)  # type: ignore[union-attr]
        config = context.bot_data["config"]
        sb = context.bot_data["sb_client"]
        try:
            url = await create_pyrus_ticket(repo, sb, config, user_id, payload_data)
        except Exception as e:
            logger.error("Pyrus ticket creation failed: %s", e, exc_info=e)
            await query.edit_message_text(f"❌ Failed to create ticket: {e}")
            return
        if query.message:
            await query.message.reply_text(
                f"✅ Pyrus ticket created:\n{url}", disable_web_page_preview=True
            )


async def _apply_pending_edit(
    update: Update, repo: ReceiptRepository, pending: dict[str, Any], value: str
) -> None:
    assert update.message
    receipt_id = pending["id"]
    field = pending["field"]
    value = value.strip()

    if field == "amount":
        try:
            amt = float(value.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Amount must be a number. Try /edit again.")
            return
        if amt <= 0:
            await update.message.reply_text("❌ Amount must be positive.")
            return
        stored: Any = f"{amt:.2f}"
    elif field == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            await update.message.reply_text("❌ Date must be YYYY-MM-DD.")
            return
        stored = value
    elif field == "currency":
        stored = value.upper()
    else:
        stored = value

    await repo.update_receipt(receipt_id, {field: stored})
    await update.message.reply_text(f"✅ #{receipt_id}: {field} → {stored}")


async def _extract_pending(
    extractor: ClaudeExtractor, pending: dict[str, Any], anchor: str
) -> ExtractedReceipt | ExtractionError:
    kind = pending["source_kind"]
    file_bytes = pending["file_bytes"]
    if kind == "pdf":
        return await extractor.extract_from_pdf(file_bytes, anchor)
    media_type = pending.get("content_type") or "image/jpeg"
    return await extractor.extract_from_image(file_bytes, media_type, anchor)


async def _process_image(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    image_bytes: bytes,
    sha: str,
    source_kind: str,
) -> None:
    assert update.message
    extractor: ClaudeExtractor = context.bot_data["extractor"]
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]
    anchor = date.today().isoformat()

    result = await extractor.extract_from_image(image_bytes, "image/jpeg", anchor)

    if isinstance(result, ExtractionError):
        await update.message.reply_text(
            "🤷 This doesn't look like a receipt. "
            "If it is, try a clearer photo."
        )
        return

    if result.date > date.today():
        await update.message.reply_text("❌ Date is in the future — please check.")
        return

    await _save_and_reply(
        update, repo, user_id, result, source_kind,
        image_bytes, "jpg", "image/jpeg", sha,
    )


async def _save_and_reply(
    update: Update,
    repo: ReceiptRepository,
    user_id: str,
    result: ExtractedReceipt,
    source_kind: str,
    file_bytes: bytes,
    ext: str,
    content_type: str,
    sha: str,
) -> None:
    assert update.message
    status = "pending_review" if result.confidence < 0.7 else "confirmed"
    saved = await repo.save_receipt(
        user_id, result, source_kind,
        file_bytes=file_bytes, file_ext=ext,
        file_content_type=content_type,
        status=status,
    )
    receipt_id = saved["id"]
    await repo.update_receipt(receipt_id, {"notes": f"sha256:{sha}"})

    open_trip_id = saved.get("trip_id")

    if status == "pending_review":
        await update.message.reply_text(
            f"🔍 Low confidence extraction for #{receipt_id}:\n\n"
            f"Provider: {result.provider or '—'}\n"
            f"Date: {result.date}\n"
            f"Amount: {result.amount} {result.currency}\n"
            f"Category: {result.category}",
            reply_markup=confirm_keyboard(receipt_id),
        )
    else:
        await update.message.reply_text(
            _format_confirmation(saved),
            reply_markup=saved_keyboard(receipt_id, open_trip_id=open_trip_id),
        )

    # Offer to merge if a recent receipt on the same date looks like the same payment
    candidate = await repo.find_merge_candidate(user_id, result.date, saved)
    if candidate:
        cand_amount = candidate.get("amount", 0)
        cand_curr = candidate.get("currency", "")
        cand_provider = candidate.get("provider") or "—"
        await update.message.reply_text(
            f"🔗 Same amount as #{candidate['id']} "
            f"({cand_provider} · {float(cand_amount):,.2f} {cand_curr}) — "
            f"looks like the same payment (bill + receipt).\n"
            f"Merge? One stays as the counted receipt; the other becomes a "
            f"supporting document (not double-counted).",
            reply_markup=merge_keyboard(receipt_id, candidate["id"]),
        )

    # Return-transfer heuristic: home-currency taxi after foreign spend → offer to close
    if (
        open_trip_id
        and result.category == "taxi"
        and result.currency.upper() == "ILS"
        and await repo.trip_has_foreign_receipts(user_id, open_trip_id)
    ):
        await update.message.reply_text(
            "🏁 Looks like a return transfer home. "
            "Close the trip and generate the final report?",
            reply_markup=saved_keyboard(receipt_id, open_trip_id=open_trip_id),
        )
