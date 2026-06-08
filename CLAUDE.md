# Receipts Bot — Claude Code Project Guide

Telegram bot that ingests expense receipts (photos, PDFs, text) via Claude Vision,
stores them in Supabase, generates AED expense reports (.xlsx), exports to Google
Drive, and optionally files a Pyrus reimbursement ticket.

See `README.md` for setup and `ARCHITECTURE.md` for the module map.

## Stack

- **Language:** Python 3.11+
- **Bot framework:** `python-telegram-bot` v21+
- **Vision/extraction:** Anthropic SDK (`claude-sonnet-4-5`)
- **Storage:** Supabase (Postgres + Storage bucket `receipts`)
- **Excel:** `openpyxl`
- **FX:** `@fawazahmed0/currency-api` (free, no key)
- **Export:** Google Drive (ADC, `drive.file` scope)
- **Integrations:** Pyrus (optional, Dodo-specific)

## Directory map

```
src/
├── main.py                 # wires handlers, healthcheck on $PORT
├── config.py               # env vars (fails loud at startup)
├── bot/
│   ├── handlers.py         # photo/document/text capture, callback dispatch
│   ├── commands.py         # /report /list /edit /delete /undo /trip /export /close /pyrus /merge
│   └── keyboards.py        # inline keyboards
├── extraction/
│   ├── prompts.py          # extractor system prompt — has DD/MM date rules etc.
│   ├── claude_client.py    # Anthropic SDK wrapper (image/PDF/text)
│   └── schemas.py          # Pydantic models (uses _dt.time to avoid shadowing)
├── storage/
│   ├── supabase_client.py  # httpx wrapper over PostgREST + Storage API
│   └── repository.py       # receipt + trip CRUD, dedup, auto-trip-attach, merge
├── report/
│   ├── builder.py          # openpyxl report; compute_aed_total helper
│   ├── fx.py               # currency-api client with 8-day fallback
│   └── recalc.py           # LibreOffice headless recalc (no-op if missing)
├── export/
│   └── drive.py            # Drive uploader using ADC
├── integrations/
│   └── pyrus.py            # Pyrus client + Payment.UAE form payload builder
└── utils/
    ├── clustering.py       # date-gap trip clustering + naming
    ├── dates.py            # range parsing (YYYY-MM / DATE..DATE)
    └── dedup.py            # SHA-256 hash

supabase/migrations/        # idempotent DDL — apply via dashboard SQL Editor
                            # (the project's `db push` is blocked by a sibling
                            # open-brain migration, so don't try CLI push here)
tests/                      # 25 tests, all green; pytest-asyncio mode=auto
```

## Conventions

- **Models**: extractor on `claude-sonnet-4-5`. Configurable via `ANTHROPIC_MODEL_EXTRACTOR`.
- **User**: Israel-based; home currency ILS, home airport TLV. Trip clustering uses
  date-gap (≤2 days), not currency — ILS bookends stay with the foreign trip.
- **Pyrus** form/catalog IDs in `src/integrations/pyrus.py` are Dodo-shared (form 1135007,
  catalog item_ids captured from a real reimbursement ticket). Personal values
  (`COUNTERPARTY_NAME`, `ASSISTANT_PERSON_ID`) come from env vars.
- **Repo visibility**: public (`izomba-lang/receipts-bot`). Don't commit secrets or
  personal infra paths — those live in `CLAUDE.local.md` (gitignored).

## When making code changes

1. Edit files in `~/receipts-bot/`
2. Run `.venv/bin/ruff check src/ && .venv/bin/python -m pytest tests/`
3. Commit and push to GitHub (`origin/main`)
4. **For prod deploy:** see `CLAUDE.local.md` for the host-specific workflow.
   Do NOT start the bot locally with `python -m src.main` while prod is running —
   Telegram allows only one getUpdates consumer per token, the local instance
   knocks prod offline with 409 Conflict.

## When debugging user-reported issues

- DB-side checks (receipts, trips, currency, dates) can be done with direct
  `curl` calls against the Supabase REST API using `SUPABASE_SERVICE_KEY` from
  `.env` — no need to touch the running bot.
- Data fixes (currency, date, merging, deleting) via REST PATCH/DELETE work the
  same way and don't disrupt prod.
- For bot-runtime issues (handler errors, polling lag), check prod logs via the
  workflow in `CLAUDE.local.md`. **Do not start a local bot** — see above.

## Migrations

Migrations in `supabase/migrations/` are documentation only. The Open Brain CLI
in `~/open-brain` is linked to the same Supabase project, but its `db push`
queue is blocked by an unrelated migration. Apply receipts-bot DDL via the
Supabase SQL Editor in the dashboard:

  https://supabase.com/dashboard/project/qtjbweggawytrzsmlwtx/sql/new
