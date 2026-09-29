"""Idempotent bulk ingest of the 2026-07/08 travel receipt batch.

The amounts in ``2026-07_08/_ingest.json`` were reconciled against the original
documents by hand, so this script deliberately does NOT re-run Claude Vision
extraction — it writes the manifest straight into Supabase. See
``2026-07_08/INSTRUCTION_FOR_CLAUDE_CODE.md`` for why (Yandex Go pages carry a
second tip receipt, and the Red Wings order is split across four passengers).

Re-running is safe: receipts are matched by the ``sha256:`` marker in ``notes``,
storage uploads overwrite the same key, and an interrupted run is repaired on
the next pass.

    python scripts/ingest_2026_07_08.py --dry-run
    python scripts/ingest_2026_07_08.py
    python scripts/ingest_2026_07_08.py --verify-only

Any later batch works the same way with ``--manifest <dir>/_ingest.json``. Its
reconciliation targets come from the manifest's ``expect`` block
(``rows``, ``report_rows``, ``currency_totals``, optional ``aed_total``);
without one, the 2026-07/08 constants below apply.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.report.builder import compute_aed_total  # noqa: E402
from src.report.fx import FxRateProvider  # noqa: E402
from src.storage.repository import ReceiptRepository  # noqa: E402
from src.storage.supabase_client import SupabaseClient  # noqa: E402

logger = logging.getLogger("ingest_2026_07_08")

DEFAULT_MANIFEST = REPO_ROOT / "2026-07_08" / "_ingest.json"

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Reconciliation targets from the instruction file.
EXPECTED_CURRENCY_TOTALS = {
    "ILS": Decimal("2150.00"),
    "RUB": Decimal("93861.00"),
    "TRY": Decimal("17514.78"),
    "EUR": Decimal("897.40"),
}
EXPECTED_ROWS = 32
EXPECTED_REPORT_ROWS = 31
EXPECTED_AED_TOTAL = Decimal("11809.34")
AED_TOLERANCE = Decimal("0.02")  # ±2 %

SHARED_BOOKING_FILE = "2026-08-04_RedWings_bilety_chek325_78390RUB.pdf"
SHARED_BOOKING_SHARE = Decimal("21640.00")


def expectations_for(manifest: dict[str, Any]) -> dict[str, Any]:
    """Reconciliation targets: the manifest's ``expect`` block, or the 2026-07/08
    constants for the original manifest that predates it."""
    spec = manifest.get("expect")
    if spec is None:
        return {
            "rows": EXPECTED_ROWS,
            "report_rows": EXPECTED_REPORT_ROWS,
            "currency_totals": EXPECTED_CURRENCY_TOTALS,
            "aed_total": EXPECTED_AED_TOTAL,
        }
    aed = spec.get("aed_total")
    return {
        "rows": int(spec["rows"]),
        "report_rows": int(spec["report_rows"]),
        "currency_totals": {
            c.upper(): Decimal(str(v)) for c, v in spec["currency_totals"].items()
        },
        "aed_total": Decimal(str(aed)) if aed is not None else None,
    }


class ReconciliationError(RuntimeError):
    """Raised when the loaded data does not match the expected totals."""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_type_for(path: Path) -> str:
    try:
        return CONTENT_TYPES[path.suffix.lower()]
    except KeyError:
        raise ReconciliationError(f"Unsupported file type: {path.name}") from None


def build_notes(sha: str, note: str | None, source: str | None) -> str:
    """``sha256:<hash>`` must stay first — ``find_by_hash`` greps for it."""
    parts = [f"sha256:{sha}"]
    if note:
        parts.append(note)
    if source:
        parts.append(f"источник: {source}")
    return " · ".join(parts)


def load_manifest(path: Path, user_id: str) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    for item in manifest["receipts"]:
        item["_path"] = base / item["file"]
    manifest["user_id"] = user_id
    return manifest


def verify_local_files(manifest: dict[str, Any]) -> None:
    """Fail before touching the DB if a file is missing or was edited."""
    problems: list[str] = []
    for item in manifest["receipts"]:
        path: Path = item["_path"]
        if not path.exists():
            problems.append(f"missing file: {item['file']}")
            continue
        actual = sha256_of(path)
        if actual != item["sha256"]:
            problems.append(
                f"sha256 mismatch for {item['file']}: "
                f"manifest {item['sha256']}, file {actual}"
            )
    if problems:
        raise ReconciliationError("Local manifest check failed:\n  " + "\n  ".join(problems))
    logger.info("Local check OK: %d files present, all hashes match", len(manifest["receipts"]))


async def ensure_trips(
    repo: ReceiptRepository, user_id: str, manifest: dict[str, Any], dry_run: bool
) -> dict[str, int]:
    existing = {t["name"]: t for t in await repo.list_trips(user_id)}
    trip_ids: dict[str, int] = {}
    for spec in manifest["trips"]:
        name = spec["name"]
        found = existing.get(name)
        if found:
            trip_ids[name] = found["id"]
            logger.info("Trip reused: %-20s id=%s", name, found["id"])
            continue
        if dry_run:
            logger.info("Trip would be created: %-20s %s → %s",
                        name, spec["start_date"], spec["end_date"])
            trip_ids[name] = -1
            continue
        created = await repo.create_trip(
            user_id,
            name,
            date.fromisoformat(spec["start_date"]),
            date.fromisoformat(spec["end_date"]),
            spec.get("notes"),
        )
        trip_ids[name] = created["id"]
        logger.info("Trip created: %-20s id=%s", name, created["id"])
    return trip_ids


def row_for(item: dict[str, Any], user_id: str, trip_id: int | None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "date": item["date"],
        "time": item.get("time"),
        "provider": item.get("provider"),
        "category": item.get("category") or "other",
        "amount": str(item["amount"]),
        "currency": item["currency"].upper(),
        "from_location": item.get("from_location"),
        "to_location": item.get("to_location"),
        "receipt_number": item.get("receipt_number"),
        "trip_number": item.get("trip_number"),
        "payment_method": item.get("payment_method"),
        "source_kind": item.get("source_kind") or "pdf",
        # Reconciled by a human against the originals, not guessed by a model.
        "confidence": 1.0,
        "status": "confirmed",
        "trip_id": trip_id,
        "notes": build_notes(item["sha256"], item.get("note"), item.get("source")),
    }


async def upload_original(
    client: SupabaseClient, bucket: str, user_id: str, receipt_id: int, path: Path
) -> str:
    key = f"{user_id}/{receipt_id}{path.suffix.lower()}"
    return await client.upload_file(
        bucket, key, path.read_bytes(), content_type_for(path)
    )


async def ingest_receipts(
    client: SupabaseClient,
    repo: ReceiptRepository,
    bucket: str,
    user_id: str,
    manifest: dict[str, Any],
    trip_ids: dict[str, int],
    dry_run: bool,
) -> dict[str, dict[str, Any]]:
    """Returns {sha256: db_row} for every manifest entry."""
    by_sha: dict[str, dict[str, Any]] = {}
    inserted = skipped = repaired = 0

    for item in manifest["receipts"]:
        sha = item["sha256"]
        path: Path = item["_path"]
        trip_id = trip_ids[item["trip"]]

        existing = await repo.find_by_hash(user_id, sha)
        if existing:
            # An earlier run may have died between the insert and the upload.
            if not existing.get("source_file_id") and not dry_run:
                storage_path = await upload_original(
                    client, bucket, user_id, existing["id"], path
                )
                existing = await repo.update_receipt(
                    existing["id"], {"source_file_id": storage_path}
                )
                repaired += 1
                logger.info("REPAIR  id=%-4s %s (uploaded missing original)",
                            existing["id"], item["file"])
            else:
                skipped += 1
                logger.info("SKIP    id=%-4s %s (already ingested)",
                            existing["id"], item["file"])
            by_sha[sha] = existing
            continue

        if dry_run:
            logger.info("WOULD INSERT  %s %s %s %s",
                        item["date"], item["amount"], item["currency"], item["file"])
            by_sha[sha] = {**row_for(item, user_id, trip_id), "id": -1}
            continue

        saved = await client.insert("receipts", row_for(item, user_id, trip_id))
        storage_path = await upload_original(client, bucket, user_id, saved["id"], path)
        saved = await repo.update_receipt(saved["id"], {"source_file_id": storage_path})
        inserted += 1
        logger.info("INSERT  id=%-4s %s %10s %s  %s",
                    saved["id"], item["date"], item["amount"], item["currency"], item["file"])
        by_sha[sha] = saved

    logger.info("Receipts: %d inserted, %d skipped, %d repaired", inserted, skipped, repaired)
    return by_sha


async def link_attachments(
    repo: ReceiptRepository, manifest: dict[str, Any],
    by_sha: dict[str, dict[str, Any]], dry_run: bool,
) -> None:
    """Supporting documents (e-tickets) hang off the receipt that paid for them
    and drop out of report totals."""
    by_basename = {Path(i["file"]).name: i["sha256"] for i in manifest["receipts"]}
    for item in manifest["receipts"]:
        parent_file = item.get("attachment_of")
        if not parent_file:
            continue
        parent_sha = by_basename.get(parent_file)
        if not parent_sha:
            raise ReconciliationError(
                f"{item['file']} points at unknown parent {parent_file}"
            )
        child = by_sha[item["sha256"]]
        parent = by_sha[parent_sha]
        if child.get("parent_id") == parent["id"]:
            logger.info("Attachment already linked: id=%s → parent id=%s",
                        child["id"], parent["id"])
            continue
        if dry_run:
            logger.info("WOULD LINK  %s → parent %s", item["file"], parent_file)
            continue
        await repo.merge_receipts(parent["id"], child["id"])
        logger.info("LINK    id=%s → parent id=%s (%s)",
                    child["id"], parent["id"], parent_file)


async def reconcile(
    client: SupabaseClient,
    repo: ReceiptRepository,
    user_id: str,
    manifest: dict[str, Any],
    trip_ids: dict[str, int],
    check_storage: bool,
) -> list[dict[str, Any]]:
    """Re-read everything from the DB and compare against the expected totals."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing: list[str] = []
    for item in manifest["receipts"]:
        found = await repo.find_by_hash(user_id, item["sha256"])
        if not found:
            missing.append(item["file"])
        else:
            pairs.append((item, found))
    rows = [row for _, row in pairs]
    expect = expectations_for(manifest)

    problems: list[str] = []
    if missing:
        problems.append(f"{len(missing)} manifest entries not in DB: {missing}")
    if len(rows) != expect["rows"]:
        problems.append(f"expected {expect['rows']} rows, found {len(rows)}")

    deleted = [r["id"] for r in rows if r.get("status") == "deleted"]
    if deleted:
        problems.append(f"rows marked deleted: {deleted}")

    no_file = [r["id"] for r in rows if not r.get("source_file_id")]
    if no_file:
        problems.append(f"rows without source_file_id: {no_file}")

    no_trip = [r["id"] for r in rows if not r.get("trip_id")]
    if no_trip:
        problems.append(f"rows without trip_id: {no_trip}")

    wrong_trip = [
        f"id={r['id']} {item['file']} → trip_id={r.get('trip_id')}, "
        f"expected {trip_ids.get(item['trip'])}"
        for item, r in pairs
        if r.get("trip_id") != trip_ids.get(item["trip"])
    ]
    if wrong_trip:
        problems.append(f"rows on the wrong trip: {wrong_trip}")

    reportable = [r for r in rows if not r.get("parent_id")]
    if len(reportable) != expect["report_rows"]:
        problems.append(
            f"expected {expect['report_rows']} reportable rows, found {len(reportable)}"
        )

    totals: dict[str, Decimal] = defaultdict(Decimal)
    for r in reportable:
        totals[r["currency"].upper()] += Decimal(str(r["amount"]))
    for currency, expected in expect["currency_totals"].items():
        actual = totals.get(currency, Decimal("0"))
        mark = "OK " if actual == expected else "!! "
        logger.info("%s%-4s expected %12s   actual %12s", mark, currency, expected, actual)
        if actual != expected:
            problems.append(f"{currency}: expected {expected}, got {actual}")
    extra = set(totals) - set(expect["currency_totals"])
    if extra:
        problems.append(f"unexpected currencies: {sorted(extra)}")

    # The one line most likely to be wrong: receipt #325 is a shared
    # four-passenger booking of 78 390 RUB, of which only ZOMBA ILIA's share
    # is reimbursable.
    for item, r in pairs:
        if Path(item["file"]).name != SHARED_BOOKING_FILE:
            continue
        if Decimal(str(r["amount"])) != SHARED_BOOKING_SHARE:
            problems.append(
                f"Red Wings #325 (id={r['id']}) is {r['amount']}, "
                f"expected {SHARED_BOOKING_SHARE}"
            )

    if check_storage:
        for r in rows:
            try:
                blob = await client.download_file(r["source_file_id"])
            except Exception as exc:  # noqa: BLE001 - report, don't crash the sweep
                problems.append(f"id={r['id']} storage unreadable: {exc}")
                continue
            if hashlib.sha256(blob).hexdigest() not in r["notes"]:
                problems.append(f"id={r['id']} stored file does not match its sha256")
        logger.info("Storage check: %d originals downloaded and hash-verified", len(rows))

    if problems:
        raise ReconciliationError("Reconciliation failed:\n  " + "\n  ".join(problems))

    logger.info("Reconciliation OK: %d rows, %d reportable", len(rows), len(reportable))
    return reportable


async def report_aed_total(
    reportable: list[dict[str, Any]], expected: Decimal | None
) -> None:
    fx = FxRateProvider()
    try:
        total = Decimal(str(await compute_aed_total(reportable, fx)))
    finally:
        await fx.close()
    if expected is None:
        logger.info("AED total: %s (no expected value in the manifest)", total)
        return
    delta = abs(total - expected) / expected
    logger.info("AED total: %s (expected ≈ %s, delta %.2f %%)",
                total, expected, delta * 100)
    if delta > AED_TOLERANCE:
        raise ReconciliationError(
            f"AED total {total} is {delta * 100:.2f} % off the expected "
            f"{expected} (tolerance ±{AED_TOLERANCE * 100:.0f} %)"
        )


async def main_async(args: argparse.Namespace) -> int:
    load_dotenv(REPO_ROOT / ".env")
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    bucket = os.environ.get("SUPABASE_BUCKET", "receipts")
    user_id = str(int(os.environ["TELEGRAM_OWNER_ID"]))

    manifest = load_manifest(args.manifest, user_id)
    verify_local_files(manifest)

    client = SupabaseClient(url, key)
    repo = ReceiptRepository(client, bucket)
    try:
        if not args.verify_only:
            trip_ids = await ensure_trips(repo, user_id, manifest, args.dry_run)
            by_sha = await ingest_receipts(
                client, repo, bucket, user_id, manifest, trip_ids, args.dry_run
            )
            await link_attachments(repo, manifest, by_sha, args.dry_run)
            if args.dry_run:
                logger.info("Dry run — nothing was written.")
                return 0
        else:
            trip_ids = {
                t["name"]: t["id"]
                for t in await repo.list_trips(user_id)
                if t["name"] in {s["name"] for s in manifest["trips"]}
            }

        reportable = await reconcile(
            client, repo, user_id, manifest, trip_ids, check_storage=not args.skip_storage_check
        )
        await report_aed_total(reportable, expectations_for(manifest)["aed_total"])
    except ReconciliationError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be written, touch nothing")
    parser.add_argument("--verify-only", action="store_true",
                        help="skip ingest, only reconcile what is already in the DB")
    parser.add_argument("--skip-storage-check", action="store_true",
                        help="do not re-download every original from the bucket")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
