"""File the Pyrus reimbursement ticket for one trip from the laptop.

The same ticket the bot files after ``/close`` → «Create ticket», but without
going through Telegram — useful after a bulk ingest, when the receipts went
straight into Supabase.

Preview is the default; nothing is sent to Pyrus without ``--create``:

    python scripts/file_pyrus_ticket.py --trip 9
    python scripts/file_pyrus_ticket.py --trip 9 --purpose "taxi during business trip (X)"
    python scripts/file_pyrus_ticket.py --trip 9 --purpose "..." --create

The preview also writes the .xlsx report next to ``--report-dir`` so it can be
checked before filing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.bot.commands import create_pyrus_ticket  # noqa: E402
from src.config import Config  # noqa: E402
from src.integrations.pyrus import AED_TO_RUB_RATE  # noqa: E402
from src.report.builder import build_report, compute_aed_total  # noqa: E402
from src.report.fx import FxRateProvider  # noqa: E402
from src.storage.repository import ReceiptRepository  # noqa: E402
from src.storage.supabase_client import SupabaseClient  # noqa: E402


def default_purpose(trip_name: str) -> str:
    return f"flights, taxi and meals during business trip ({trip_name})"


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv(REPO_ROOT / ".env")
    cfg = Config.from_env()
    if not (cfg.pyrus_login and cfg.pyrus_security_key):
        print("Pyrus is not configured: PYRUS_LOGIN / PYRUS_SECURITY_KEY missing in .env")
        return 1
    user_id = str(cfg.telegram_owner_id)

    sb = SupabaseClient(cfg.supabase_url, cfg.supabase_service_key)
    repo = ReceiptRepository(sb, cfg.supabase_bucket)
    fx = FxRateProvider()
    try:
        trip = await repo.get_trip(user_id, args.trip)
        if not trip:
            print(f"Trip {args.trip} not found")
            return 1
        receipts = await repo.get_receipts_by_trip(user_id, trip["id"])
        if not receipts:
            print(f"Trip «{trip['name']}» has no receipts")
            return 1

        aed_total = await compute_aed_total(receipts, fx)
        purpose = args.purpose or default_purpose(trip["name"])
        payment_date = args.payment_date or trip["end_date"]

        print(f"Trip: {trip['name']} (id={trip['id']}) {trip['start_date']} → {trip['end_date']}")
        for r in receipts:
            rate = await fx.get_rate(date.fromisoformat(r["date"]), r["currency"])
            aed = float(r["amount"]) * float(rate)
            print(f"  #{r['id']:<4} {r['date']} {str(r.get('time') or '')[:5]:5} "
                  f"{(r.get('provider') or '')[:30]:30} {r['amount']:>12} {r['currency']}"
                  f"  ≈ {aed:9.2f} AED")
        print(f"\nAmount:        {aed_total:,.2f} AED")
        print(f"Amount in RUB: {aed_total * AED_TO_RUB_RATE:,.2f} (rate {AED_TO_RUB_RATE})")
        print(f"Purpose:       {purpose}")
        print(f"Payment date:  {payment_date}")
        print(f"Counterparty:  {cfg.pyrus_counterparty_name}")
        print(f"Subscriber:    {cfg.pyrus_assistant_person_id}")

        if args.report_dir:
            out = Path(args.report_dir) / f"{trip['end_date']}_trip-{trip['id']}_report.xlsx"
            out.parent.mkdir(parents=True, exist_ok=True)
            await build_report(
                receipts,
                date.fromisoformat(trip["start_date"]),
                date.fromisoformat(trip["end_date"]),
                out, fx, title_override=trip["name"],
            )
            print(f"Report:        {out}")

        if not args.create:
            print("\nPreview only — nothing sent. Re-run with --create to file the ticket.")
            return 0

        url = await create_pyrus_ticket(repo, sb, cfg, user_id, {
            "trip_id": trip["id"],
            "aed_total": aed_total,
            "payment_date": payment_date,
            "purpose": purpose,
        })
        print(f"\nTICKET {url}")
        return 0
    finally:
        await fx.close()
        await sb.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trip", type=int, required=True, help="trips.id")
    parser.add_argument("--purpose", help="Purpose field; default mentions flights, taxi, meals")
    parser.add_argument("--payment-date", help="YYYY-MM-DD; default is the trip end date")
    parser.add_argument("--report-dir", help="also write the .xlsx report here")
    parser.add_argument("--create", action="store_true", help="actually create the ticket")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
