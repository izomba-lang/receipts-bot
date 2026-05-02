# Architecture

High-level map of how the receipts bot is wired. Read this before diving into code.

## Data flow (capture)

```
Telegram → bot/handlers.py
       ↓
       Photo / PDF / Text
       ↓
extraction/claude_client.py  ── calls Anthropic Sonnet with strict JSON schema
       ↓
extraction/schemas.py        ── Pydantic validates the response
       ↓
storage/repository.py        ── insert into Supabase + upload original to Storage
       ↓                        + auto-attach to active trip (find_trip_for_date)
       ↓
bot/handlers.py              ── reply with confirmation card
```

## Data flow (report)

```
/report YYYY-MM | /report trip:Name
       ↓
bot/commands.py              ── parse arg, fetch receipts
       ↓
report/fx.py                 ── for each (date, currency), get AED rate via currency-api
       ↓                        (per-request cache, 8-day fallback for weekends/holes)
       ↓
report/builder.py            ── openpyxl: header, body rows with =E*G formulas, TOTAL, subtotals, notes
       ↓
report/recalc.py             ── LibreOffice headless recalc so formulas are pre-computed
       ↓
Telegram                     ── send_document(.xlsx)
```

## Trip auto-clustering

`utils/clustering.py` — `cluster_receipts(rows)`:
- Sort by date
- Walk: new cluster when `gap > 2 days` OR previous receipt was `category=flight`
- Naming via `suggest_trip_name`: dominant non-ILS currency + month, e.g. `Trip RUB Apr 2026`. Falls back to `Local <Month> <Year>` if everything is ILS.

Why date-gap, not currency? User is Israel-based — every trip starts/ends with ILS taxi to Ben Gurion. A pure currency-split would shatter one trip into three. Date-gap keeps the bookends with the trip body.

## Modules

| Module | Responsibility |
|---|---|
| `src/config.py` | Env var validation (fails loud at startup) |
| `src/main.py` | Wires Application: handlers, commands, callback router, healthcheck on `$PORT` |
| `src/bot/handlers.py` | Photo/document/text capture, confirmation card, callback dispatch |
| `src/bot/commands.py` | `/report` `/list` `/edit` `/delete` `/undo` `/cancel` `/trip` (sub-cmds: new/list/current/suggest/assign/unassign) |
| `src/bot/keyboards.py` | Inline keyboards (confirm, duplicate, trip suggest) |
| `src/extraction/prompts.py` | System prompt for receipt extractor — includes "no service class" rule |
| `src/extraction/claude_client.py` | Anthropic SDK wrapper — image / PDF / text variants |
| `src/extraction/schemas.py` | Pydantic models for extracted receipts. **Note:** uses `_dt.time` (not bare `time`) to avoid Pydantic field-name shadowing |
| `src/storage/supabase_client.py` | Thin httpx wrapper over PostgREST + Storage API |
| `src/storage/repository.py` | Receipt + Trip CRUD, dedup-by-hash, auto-trip-attach |
| `src/report/builder.py` | openpyxl report generation matching the AED template |
| `src/report/fx.py` | Currency-api client with 8-day fallback walk |
| `src/report/recalc.py` | LibreOffice headless recalc (no-op if `soffice` not installed) |
| `src/utils/clustering.py` | Date-gap based trip clustering + naming |
| `src/utils/dates.py` | Range parsing for `/report YYYY-MM` and ranges |
| `src/utils/dedup.py` | SHA-256 content hash |

## Database schema

Two tables in the shared Open Brain Supabase project (`qtjbweggawytrzsmlwtx`):

- `receipts` — one row per expense. `trip_id` is nullable FK to `trips`. `notes` field doubles as place to stash `sha256:HASH` for dedup lookup.
- `trips` — user-defined or auto-suggested trips. Receipts auto-attach when their date falls within `[start_date, end_date]`.

Migrations live in `supabase/migrations/` AND mirrored to `~/open-brain/supabase/migrations/` so the open-brain CLI (which is linked to the project) can `supabase db push` them.

Originals go to Supabase Storage bucket `receipts/<user_id>/<receipt_id>.<ext>`.

## Auth model

Single-user. `TELEGRAM_OWNER_ID` env var pins the bot to one chat. `@owner_only` decorator on every handler returns "access denied" for any other user.

Supabase access uses `service_role` key — bypasses RLS. RLS is enabled on both tables purely as belt-and-braces in case the anon key ever leaks.

## What's NOT here (deliberately)

- Multi-user, billing, account creation
- OCR fallback if Claude is down
- Webhook mode (long polling is enough for personal volume)
- Web dashboard
- Auto-categorization / spending insights

These are noted in README "Future work" but should stay out of scope unless real demand emerges.

## Local dev quickref

```bash
cd ~/receipts-bot
uv venv --python 3.11 .venv
uv pip install -e ".[dev]"
cp .env.example .env  # fill in values
.venv/bin/python -m pytest tests/    # 25 tests, all green
.venv/bin/ruff check src/
PORT=8765 .venv/bin/python -m src.main
```
