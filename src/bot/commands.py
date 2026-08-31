from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.bot.handlers import CURRENCY_SYMBOLS, owner_only
from src.config import Config
from src.export.drive import DriveClient
from src.integrations.pyrus import PyrusClient, build_payment_payload
from src.report.builder import build_report, compute_aed_total
from src.report.fx import FxRateProvider
from src.storage.repository import ReceiptRepository
from src.storage.supabase_client import SupabaseClient
from src.utils.clustering import cluster_receipts, suggest_trip_name
from src.utils.dates import parse_report_range

logger = logging.getLogger(__name__)


async def _resolve_trip(
    repo: ReceiptRepository, user_id: str, query: str
) -> dict[str, Any] | None:
    if query.isdigit():
        return await repo.get_trip(user_id, int(query))
    return await repo.find_trip_by_name(user_id, query)


def _parse_quoted_args(text: str) -> list[str]:
    import shlex
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()

EDITABLE_FIELDS = {
    "amount", "currency", "category", "provider", "date",
    "from_location", "to_location", "payment_method", "notes",
    "time", "receipt_number", "trip_number",
}


@owner_only
async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    args = context.args
    arg = args[0] if args else None

    trip: dict[str, Any] | None = None
    receipts: list[dict[str, Any]]
    label: str
    filename: str

    if arg and arg.startswith("trip:"):
        query = arg[len("trip:"):]
        trip = await _resolve_trip(repo, user_id, query)
        if not trip:
            await update.message.reply_text(f"❌ Trip not found: {query}")
            return
        start = date.fromisoformat(trip["start_date"])
        end = date.fromisoformat(trip["end_date"])
        receipts = await repo.get_receipts_by_trip(user_id, trip["id"])
        label = trip["name"]
        safe_name = "".join(c if c.isalnum() else "_" for c in trip["name"])
        filename = f"expense_report_{safe_name}.xlsx"
    else:
        try:
            start, end = parse_report_range(arg)
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}")
            return
        receipts = await repo.get_receipts_in_range(user_id, start, end)
        label = f"{start} → {end}"
        filename = f"expense_report_{start}_{end}.xlsx"

    await update.message.reply_text(f"📊 Generating report for {label}…")

    if not receipts:
        await update.message.reply_text("No receipts found for this period.")
        return

    fx = FxRateProvider()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / filename
            override = label if trip else None
            await build_report(receipts, start, end, path, fx, title_override=override)

            await update.message.reply_document(
                document=path.open("rb"),
                filename=filename,
                caption=f"Expense report: {label} ({len(receipts)} receipts)",
            )
    finally:
        await fx.close()


@owner_only
async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    args = context.args
    arg = args[0] if args else None

    receipts: list[dict[str, Any]]
    label: str

    if arg and arg.startswith("trip:"):
        query = arg[len("trip:"):]
        trip = await _resolve_trip(repo, user_id, query)
        if not trip:
            await update.message.reply_text(f"❌ Trip not found: {query}")
            return
        receipts = await repo.get_receipts_by_trip(user_id, trip["id"])
        label = f"trip {trip['name']}"
    else:
        try:
            start, end = parse_report_range(arg)
        except ValueError as e:
            await update.message.reply_text(f"❌ {e}")
            return
        receipts = await repo.get_receipts_in_range(user_id, start, end)
        label = f"{start} → {end}"

    if not receipts:
        await update.message.reply_text("No receipts found for this period.")
        return

    lines: list[str] = [f"📋 Receipts for {label}:\n"]
    for r in receipts:
        rid = r["id"]
        d = r.get("date", "")
        provider = r.get("provider") or "—"
        amount = r.get("amount", 0)
        currency = r.get("currency", "")
        status = r.get("status", "")
        sym = CURRENCY_SYMBOLS.get(currency, "")
        amount_str = f"{sym}{float(amount):,.2f}" if sym else f"{float(amount):,.2f} {currency}"
        flag = " ⚠️" if status == "pending_review" else ""
        lines.append(f"  #{rid} · {d} · {provider} · {amount_str}{flag}")

    await update.message.reply_text("\n".join(lines))


@owner_only
async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    args = context.args or []

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /edit <id> <field>=<value>\n"
            f"Fields: {', '.join(sorted(EDITABLE_FIELDS))}"
        )
        return

    try:
        receipt_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid receipt ID.")
        return

    updates: dict[str, Any] = {}
    for part in args[1:]:
        if "=" not in part:
            await update.message.reply_text(f"❌ Invalid format: {part}. Use field=value.")
            return
        field, value = part.split("=", 1)
        if field not in EDITABLE_FIELDS:
            await update.message.reply_text(
                f"❌ Unknown field: {field}. "
                f"Allowed: {', '.join(sorted(EDITABLE_FIELDS))}"
            )
            return

        if field == "amount":
            try:
                v = Decimal(value)
                if v <= 0:
                    await update.message.reply_text("❌ Amount must be positive.")
                    return
                updates[field] = str(v)
            except InvalidOperation:
                await update.message.reply_text("❌ Invalid amount.")
                return
        elif field == "date":
            try:
                date.fromisoformat(value)
                updates[field] = value
            except ValueError:
                await update.message.reply_text("❌ Invalid date. Use YYYY-MM-DD.")
                return
        else:
            updates[field] = value

    await repo.update_receipt(receipt_id, updates)
    fields = ", ".join(f"{k}={v}" for k, v in updates.items())
    await update.message.reply_text(f"✅ Receipt #{receipt_id} updated: {fields}")


@owner_only
async def handle_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    args = context.args or []

    if not args:
        await update.message.reply_text("Usage: /delete <id>")
        return

    try:
        receipt_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid receipt ID.")
        return

    context.user_data["last_deleted"] = receipt_id  # type: ignore[index]
    await repo.soft_delete(receipt_id)
    await update.message.reply_text(f"🗑 Receipt #{receipt_id} deleted. /undo to restore.")


@owner_only
async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    last = context.user_data.get("last_deleted")  # type: ignore[union-attr]
    if last:
        await repo.restore(int(last))
        context.user_data.pop("last_deleted", None)  # type: ignore[union-attr]
        await update.message.reply_text(f"♻️ Receipt #{last} restored.")
        return

    last_row = await repo.get_last_deleted(user_id)
    if last_row:
        await repo.restore(last_row["id"])
        await update.message.reply_text(f"♻️ Receipt #{last_row['id']} restored.")
    else:
        await update.message.reply_text("Nothing to undo.")


@owner_only
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    if context.user_data:
        context.user_data.clear()  # type: ignore[union-attr]
    await update.message.reply_text("Cleared. Ready for new input.")


@owner_only
async def handle_trip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message and update.message.text
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    raw = update.message.text[len("/trip"):].strip()
    parts = _parse_quoted_args(raw)

    if not parts:
        await update.message.reply_text(
            "Usage:\n"
            "/trip new \"Name\" YYYY-MM-DD..YYYY-MM-DD\n"
            "/trip list\n"
            "/trip current\n"
            "/trip suggest — auto-cluster unassigned receipts into trips\n"
            "/trip assign <receipt_id> <trip_id|name>\n"
            "/trip unassign <receipt_id>"
        )
        return

    sub = parts[0].lower()

    if sub == "new":
        if len(parts) < 3:
            await update.message.reply_text(
                "Usage: /trip new \"Name\" YYYY-MM-DD..YYYY-MM-DD"
            )
            return
        name = parts[1]
        range_str = parts[2]
        if ".." not in range_str:
            await update.message.reply_text("❌ Range must be YYYY-MM-DD..YYYY-MM-DD")
            return
        try:
            s_str, e_str = range_str.split("..", 1)
            start = date.fromisoformat(s_str)
            end = date.fromisoformat(e_str)
        except ValueError:
            await update.message.reply_text("❌ Invalid date format. Use YYYY-MM-DD..YYYY-MM-DD")
            return
        if end < start:
            await update.message.reply_text("❌ End date is before start date.")
            return
        trip = await repo.create_trip(user_id, name, start, end)

        # Auto-attach existing unassigned receipts whose date falls in range
        unassigned = await repo.get_unassigned_receipts(user_id)
        in_range = [
            r for r in unassigned
            if start <= date.fromisoformat(r["date"]) <= end
        ]
        for r in in_range:
            await repo.assign_trip(r["id"], trip["id"])

        msg = (
            f"✅ Trip created: #{trip['id']} \"{trip['name']}\" "
            f"({trip['start_date']} → {trip['end_date']})"
        )
        if in_range:
            msg += f"\n🔗 Auto-attached {len(in_range)} existing receipts."
        await update.message.reply_text(msg)

    elif sub == "list":
        trips = await repo.list_trips(user_id)
        if not trips:
            await update.message.reply_text("No trips yet. Create with /trip new")
            return
        lines = ["✈️ Trips:\n"]
        for t in trips:
            lines.append(f"  #{t['id']} \"{t['name']}\" · {t['start_date']} → {t['end_date']}")
        await update.message.reply_text("\n".join(lines))

    elif sub == "current":
        today = date.today()
        trip = await repo.find_trip_for_date(user_id, today)
        if trip:
            await update.message.reply_text(
                f"✈️ Current trip: #{trip['id']} \"{trip['name']}\" "
                f"({trip['start_date']} → {trip['end_date']})"
            )
        else:
            await update.message.reply_text(f"No active trip on {today}.")

    elif sub == "assign":
        if len(parts) < 3:
            await update.message.reply_text("Usage: /trip assign <receipt_id> <trip_id|name>")
            return
        try:
            receipt_id = int(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid receipt ID.")
            return
        trip = await _resolve_trip(repo, user_id, parts[2])
        if not trip:
            await update.message.reply_text(f"❌ Trip not found: {parts[2]}")
            return
        await repo.assign_trip(receipt_id, trip["id"])
        await update.message.reply_text(
            f"✅ Receipt #{receipt_id} assigned to \"{trip['name']}\""
        )

    elif sub == "suggest":
        receipts = await repo.get_unassigned_receipts(user_id)
        if not receipts:
            await update.message.reply_text("No unassigned receipts to cluster.")
            return

        clusters = cluster_receipts(receipts)
        proposals: list[dict[str, Any]] = []
        lines = ["✈️ Suggested trips:\n"]
        for i, cluster in enumerate(clusters, 1):
            name = suggest_trip_name(cluster)
            first = str(cluster[0]["date"])
            last = str(cluster[-1]["date"])
            currencies = sorted({(r.get("currency") or "").upper() for r in cluster})
            proposals.append({
                "name": name,
                "start": first,
                "end": last,
                "receipt_ids": [r["id"] for r in cluster],
            })
            lines.append(
                f"{i}. \"{name}\" — {first} → {last} "
                f"({len(cluster)} receipts, {'/'.join(currencies)})"
            )

        proposal_id = uuid.uuid4().hex[:8]
        context.user_data[f"trip_proposal_{proposal_id}"] = proposals  # type: ignore[index]

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✓ Create all", callback_data=f"trip_create_all:{proposal_id}"
            ),
            InlineKeyboardButton(
                "✗ Cancel", callback_data=f"trip_cancel:{proposal_id}"
            ),
        ]])
        lines.append("\nCreate these trips and attach receipts?")
        await update.message.reply_text("\n".join(lines), reply_markup=keyboard)

    elif sub == "unassign":
        if len(parts) < 2:
            await update.message.reply_text("Usage: /trip unassign <receipt_id>")
            return
        try:
            receipt_id = int(parts[1])
        except ValueError:
            await update.message.reply_text("❌ Invalid receipt ID.")
            return
        await repo.assign_trip(receipt_id, None)
        await update.message.reply_text(f"✅ Receipt #{receipt_id} unassigned from trip.")

    else:
        await update.message.reply_text(f"❌ Unknown subcommand: {sub}")


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def _receipt_filename(idx: int, r: dict[str, Any]) -> str:
    d = str(r.get("date", "nodate"))
    provider = _safe_filename((r.get("provider") or "unknown")[:30])
    amount = r.get("amount", 0)
    currency = (r.get("currency") or "").upper()
    src_path = r.get("source_file_id") or ""
    ext = src_path.rsplit(".", 1)[-1] if "." in src_path else "jpg"
    return f"{idx:02d}_{d}_{provider}_{float(amount):.0f}{currency}.{ext}"


async def run_trip_export(
    repo: ReceiptRepository, sb: SupabaseClient, user_id: str, trip: dict[str, Any]
) -> dict[str, Any]:
    """Build the xlsx report + upload all originals to a Drive folder.
    Returns {share_url, uploaded, n_attach, skipped, receipts}. Raises if empty."""
    receipts = await repo.get_receipts_by_trip(user_id, trip["id"])
    all_files = await repo.get_receipts_by_trip(
        user_id, trip["id"], include_attachments=True
    )
    if not receipts:
        raise ValueError("Trip has no receipts.")

    start = date.fromisoformat(trip["start_date"])
    end = date.fromisoformat(trip["end_date"])

    drive = DriveClient()
    expenses_root = drive.find_or_create_folder("Expenses")
    folder_name = f"{trip['name']} ({trip['start_date']} → {trip['end_date']})"
    folder_id = drive.create_folder(folder_name, parent_id=expenses_root)
    logger.info(
        "Created Drive folder %s inside Expenses (%s/%s)",
        folder_name, expenses_root, folder_id,
    )

    fx = FxRateProvider()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / f"{_safe_filename(trip['name'])}_report.xlsx"
            await build_report(
                receipts, start, end, xlsx_path, fx, title_override=trip["name"]
            )
            drive.upload_file(
                xlsx_path.name,
                xlsx_path.read_bytes(),
                folder_id,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
            )
    finally:
        await fx.close()

    uploaded = 0
    skipped = 0
    for idx, r in enumerate(all_files, 1):
        src = r.get("source_file_id")
        if not src:
            skipped += 1
            continue
        try:
            data = await sb.download_file(src)
            fname = _receipt_filename(idx, r)
            if r.get("parent_id"):
                stem, _, ext = fname.rpartition(".")
                fname = f"{stem}_attachment.{ext}"
            drive.upload_file(fname, data, folder_id)
            uploaded += 1
        except Exception as e:
            logger.error("Failed to upload receipt #%d: %s", r.get("id"), e)
            skipped += 1

    share_url = drive.make_shareable(folder_id)
    n_attach = len(all_files) - len(receipts)
    return {
        "share_url": share_url,
        "uploaded": uploaded,
        "n_attach": n_attach,
        "skipped": skipped,
        "receipts": receipts,
    }


def format_export_summary(trip: dict[str, Any], res: dict[str, Any]) -> str:
    n_attach = res["n_attach"]
    skipped = res["skipped"]
    return (
        f"✅ Export complete: \"{trip['name']}\"\n\n"
        f"📊 Report: 1 xlsx\n"
        f"🧾 Receipts: {res['uploaded']} uploaded"
        f"{f' (incl. {n_attach} attachments)' if n_attach else ''}"
        f"{f', {skipped} skipped (no original)' if skipped else ''}\n\n"
        f"🔗 {res['share_url']}\n\n"
        f"Anyone with the link can view."
    )


def build_forwardable_summary(trip: dict[str, Any], res: dict[str, Any]) -> str:
    """Clean Russian summary the user can forward to an assistant/finance."""
    from collections import defaultdict

    by_cur: dict[str, float] = defaultdict(float)
    for r in res["receipts"]:
        by_cur[(r.get("currency") or "").upper()] += float(r.get("amount") or 0)

    cur_lines = "\n".join(
        f"• {cur} — {total:,.2f}"
        for cur, total in sorted(by_cur.items(), key=lambda kv: -kv[1])
    )
    n = len(res["receipts"])
    start = trip["start_date"]
    end = trip["end_date"]
    return (
        f"📋 Отчёт по затратам к возмещению — {trip['name']}\n"
        f"🗓 {start} — {end}\n\n"
        f"Все чеки и итоговый отчёт (Excel) с пересчётом в AED:\n"
        f"{res['share_url']}\n\n"
        f"Всего {n} чек(ов). Суммы по валютам:\n"
        f"{cur_lines}\n\n"
        f"Итоговая сумма к возмещению в AED — в файле Excel "
        f"(колонка Amount AED, строка TOTAL); курсы применены по дате каждого платежа.\n\n"
        f"Оригиналы всех чеков лежат в той же папке, названы по дате и поставщику."
    )


@owner_only
async def handle_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    sb: SupabaseClient = context.bot_data["sb_client"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    args = context.args
    if not args or not args[0].startswith("trip:"):
        await update.message.reply_text(
            "Usage: /export trip:<name|id>\n"
            "Generates xlsx + uploads originals to Google Drive."
        )
        return

    query = args[0][len("trip:"):]
    trip = await _resolve_trip(repo, user_id, query)
    if not trip:
        await update.message.reply_text(f"❌ Trip not found: {query}")
        return

    await update.message.reply_text(f"📤 Exporting \"{trip['name']}\"…")
    try:
        res = await run_trip_export(repo, sb, user_id, trip)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    await update.message.reply_text(
        format_export_summary(trip, res), disable_web_page_preview=True
    )


@owner_only
async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    sb: SupabaseClient = context.bot_data["sb_client"]
    config: Config = context.bot_data["config"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    trip = await repo.find_open_trip(user_id)
    if not trip:
        await update.message.reply_text("No open trip to close.")
        return

    await update.message.reply_text(
        f"🏁 Closing \"{trip['name']}\" and generating the final report…"
    )
    try:
        res = await run_trip_export(repo, sb, user_id, trip)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return
    await repo.close_trip(trip["id"])
    await update.message.reply_text(
        f"🏁 Trip \"{trip['name']}\" closed.", disable_web_page_preview=True
    )
    # Forwardable summary for the assistant / finance
    await update.message.reply_text(
        build_forwardable_summary(trip, res), disable_web_page_preview=True
    )
    # Offer to file the Pyrus reimbursement ticket
    if config.pyrus_login and config.pyrus_security_key:
        await send_pyrus_preview(update, context, trip, res["receipts"], config)


@owner_only
async def handle_merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]
    args = context.args or []

    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /merge <attachment_id> <primary_id>\n"
            "Marks the first receipt as a supporting document of the second "
            "(only the primary counts in totals)."
        )
        return

    try:
        attachment_id = int(args[0])
        primary_id = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ IDs must be numbers.")
        return

    primary = await repo.get_receipt(user_id, primary_id)
    attachment = await repo.get_receipt(user_id, attachment_id)
    if not primary or not attachment:
        await update.message.reply_text("❌ One of the receipts doesn't exist.")
        return

    await repo.merge_receipts(primary_id, attachment_id)
    await update.message.reply_text(
        f"🔗 #{attachment_id} is now a supporting document of #{primary_id}. "
        f"Only #{primary_id} counts in reports.\nUndo: /unmerge {attachment_id}"
    )


@owner_only
async def handle_unmerge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    args = context.args or []

    if not args:
        await update.message.reply_text("Usage: /unmerge <receipt_id>")
        return

    try:
        receipt_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid receipt ID.")
        return

    await repo.unmerge_receipt(receipt_id)
    await update.message.reply_text(
        f"✅ #{receipt_id} is a standalone receipt again (counts in totals)."
    )


@owner_only
async def handle_pyrus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    repo: ReceiptRepository = context.bot_data["repo"]
    config: Config = context.bot_data["config"]
    user_id = str(update.effective_user.id)  # type: ignore[union-attr]

    if not config.pyrus_login or not config.pyrus_security_key:
        await update.message.reply_text("Pyrus is not configured (missing credentials).")
        return

    args = context.args
    if not args or not args[0].startswith("trip:"):
        await update.message.reply_text("Usage: /pyrus trip:<name|id>")
        return

    trip = await _resolve_trip(repo, user_id, args[0][len("trip:"):])
    if not trip:
        await update.message.reply_text(f"❌ Trip not found: {args[0][len('trip:'):]}")
        return

    receipts = await repo.get_receipts_by_trip(user_id, trip["id"])
    if not receipts:
        await update.message.reply_text("Trip has no receipts.")
        return

    await send_pyrus_preview(update, context, trip, receipts, config)


async def send_pyrus_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    trip: dict[str, Any],
    receipts: list[dict[str, Any]],
    config: Config,
) -> None:
    """Compute AED total, stash payload for confirm callback, send preview message."""
    if not (update.message or update.effective_chat):
        return

    fx = FxRateProvider()
    try:
        aed_total = await compute_aed_total(receipts, fx)
    finally:
        await fx.close()

    rub = round(aed_total * 24, 2)
    payment_date = trip["end_date"]
    purpose = f"flights, taxi and meals during business trip ({trip['name']})"
    counterparty = config.pyrus_counterparty_name or "—"

    token = uuid.uuid4().hex[:8]
    context.user_data[f"pyrus_{token}"] = {  # type: ignore[index]
        "trip_id": trip["id"],
        "aed_total": aed_total,
        "payment_date": payment_date,
        "purpose": purpose,
    }

    preview = (
        f"📨 Pyrus ticket preview — form «Payment. UAE»\n\n"
        f"• Company: DODO BRANDS INTERNATIONAL FZCO\n"
        f"• Counterparty: {counterparty}\n"
        f"• Purpose: {purpose}\n"
        f"• Type: Reimbursement\n"
        f"• Department: Dodo Pizza.IMF.Platform\n"
        f"• Market: Dodo Pizza.International Region (w/o MENA)\n"
        f"• Expense type: Business trips_Other\n"
        f"• Amount: {aed_total:,.2f} AED\n"
        f"• Amount in RUB: {rub:,.2f} (rate 24)\n"
        f"• Payment date: {payment_date}\n"
        f"• Attachments: Excel report + all original receipts\n"
        f"• Bank details: left empty (filled downstream)\n\n"
        f"Approval route applies automatically. Create the ticket?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📨 Create ticket", callback_data=f"pyrus_go:{token}"),
        InlineKeyboardButton("✖️ Cancel", callback_data=f"pyrus_no:{token}"),
    ]])

    if update.message:
        await update.message.reply_text(preview, reply_markup=kb)
    elif update.effective_chat:
        await context.bot.send_message(
            update.effective_chat.id, preview, reply_markup=kb
        )


async def create_pyrus_ticket(
    repo: ReceiptRepository, sb: SupabaseClient, config: Config,
    user_id: str, data: dict[str, Any],
) -> str:
    """Build the xlsx, upload to Pyrus, create the reimbursement task.
    Returns the Pyrus task URL."""
    trip = await repo.get_trip(user_id, data["trip_id"])
    if not trip:
        raise ValueError("Trip not found.")
    receipts = await repo.get_receipts_by_trip(user_id, trip["id"])

    all_files = await repo.get_receipts_by_trip(
        user_id, trip["id"], include_attachments=True
    )

    pyrus = PyrusClient(config.pyrus_login, config.pyrus_security_key)
    fx = FxRateProvider()
    try:
        guids: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / f"{_safe_filename(trip['name'])}_report.xlsx"
            await build_report(
                receipts,
                date.fromisoformat(trip["start_date"]),
                date.fromisoformat(trip["end_date"]),
                xlsx_path, fx, title_override=trip["name"],
            )
            guids.append(
                await pyrus.upload_file(xlsx_path.name, xlsx_path.read_bytes())
            )

        # Upload every original receipt (photos/PDFs) too
        for idx, r in enumerate(all_files, 1):
            src = r.get("source_file_id")
            if not src:
                continue
            try:
                content = await sb.download_file(src)
                fname = _receipt_filename(idx, r)
                if r.get("parent_id"):
                    stem, _, ext = fname.rpartition(".")
                    fname = f"{stem}_attachment.{ext}"
                guids.append(await pyrus.upload_file(fname, content))
            except Exception as e:
                logger.error("Pyrus: failed to upload receipt #%s: %s", r.get("id"), e)

        payload = build_payment_payload(
            purpose=data["purpose"],
            aed_total=data["aed_total"],
            payment_date=data["payment_date"],
            counterparty_name=config.pyrus_counterparty_name or "",
            receipt_guids=guids,
            assistant_person_id=config.pyrus_assistant_person_id,
        )
        result = await pyrus.create_task(payload)
        task_id = result.get("task", {}).get("id")
        return f"https://pyrus.com/t#id{task_id}"
    finally:
        await fx.close()
        await pyrus.close()
