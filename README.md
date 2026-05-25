# Receipts Bot

Telegram bot that ingests expense receipts (photos, PDFs, text), extracts structured data via Claude Vision, stores them in Supabase, and generates AED expense reports (.xlsx). Optionally files a reimbursement ticket in Pyrus on trip close.

## Features

- 📸 Receipt extraction from photos, PDFs, and free text via Claude (vision), any language
- ✈️ Trips: group expenses by trip (auto-attach by date), close a trip to get a final report
- 🔗 Merge a bill + fiscal receipt for one payment (no double-counting)
- 🧠 Duplicate detection by file hash
- 📊 AED .xlsx report with per-date mid-market FX conversion
- ☁️ Export originals + report to a Google Drive folder with a shareable link
- 🎫 Pyrus integration: file a reimbursement ticket with attachments on trip close (Dodo-specific, optional)

## Quick start

```bash
cp .env.example .env
# Fill in all values in .env
pip install -e ".[dev]"
make run
```

## Deployment runbook (Railway)

### 1. Create the Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/newbot`, choose a name and username
3. Copy the bot token — this is `TELEGRAM_BOT_TOKEN`
4. Send `/setcommands` and paste:
   ```
   start - Get started
   help - Command reference
   report - Generate expense report
   list - List receipts for a month
   edit - Edit a receipt field
   delete - Delete a receipt
   undo - Restore last deleted receipt
   cancel - Reset state
   ```

### 2. Get your Telegram user ID

Send any message to [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID. This is `TELEGRAM_OWNER_ID`.

### 3. Apply the Supabase migration

```bash
# Using Supabase CLI (if project is linked):
supabase db push --project-ref <your-project-ref>

# Or run the SQL manually in Supabase Dashboard → SQL Editor:
# Paste contents of supabase/migrations/20260429_receipts.sql
```

Also create a Storage bucket called `receipts` in Supabase Dashboard → Storage (or via CLI).

### 4. Deploy to Railway

1. Create a new project at [railway.app](https://railway.app)
2. Connect your Git repo (or deploy from local with `railway up`)
3. Set environment variables in Railway dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_OWNER_ID`
   - `ANTHROPIC_API_KEY`
   - `SUPABASE_URL` (e.g. `https://xxxxx.supabase.co`)
   - `SUPABASE_SERVICE_KEY` (service_role key from Supabase settings)
   - `SUPABASE_BUCKET` = `receipts`
   - `LOG_LEVEL` = `INFO`
4. Railway auto-detects Python, builds, and starts the bot
5. Verify healthcheck: `curl https://<your-app>.up.railway.app/healthz`

### 5. Smoke test

1. Open your bot in Telegram
2. Send `/start` — should get a greeting
3. Send a receipt photo — should get a confirmation with extracted data
4. Send `/report` — should get an .xlsx file

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Greeting + help |
| `/help` | Full command reference |
| `/report YYYY-MM` | Generate expense report for a month |
| `/report YYYY-MM-DD..YYYY-MM-DD` | Report for a date range |
| `/report` | Report for current month |
| `/list YYYY-MM` | List receipts |
| `/edit <id> <field>=<value>` | Edit a receipt |
| `/delete <id>` | Soft-delete a receipt |
| `/undo` | Restore last deleted |
| `/cancel` | Clear pending state |

## Pyrus integration (optional, Dodo-specific)

On trip close the bot can file a reimbursement ticket in Pyrus (form "Payment. UAE"),
attaching the .xlsx report + all original receipts and notifying an assistant.

Set in `.env`:

```env
PYRUS_LOGIN=your.email@dodobrands.io
PYRUS_SECURITY_KEY=...          # Pyrus profile → Authorization
PYRUS_COUNTERPARTY_NAME=Your Name   # how you appear as the counterparty
PYRUS_ASSISTANT_PERSON_ID=866453    # Pyrus person id to add as subscriber (optional)
```

The form id and catalog item ids (company, currency, department, market, expense
type) are constants in `src/integrations/pyrus.py` — shared across Dodo, edit there
if your form differs. Leave the Pyrus vars empty to disable the integration.

Trigger manually with `/pyrus trip:<name>` — the bot shows a preview and creates the
ticket only after you confirm.

## Development

```bash
make install   # install with dev deps
make test      # run tests
make lint      # ruff + mypy
make run       # start bot locally
```

## Future work

- Multi-user support / billing
- OCR fallback for Claude downtime
- Telegram inline mode
- Reports in non-AED currencies
- Web dashboard
- Auto-categorization based on past behaviour
- Webhook mode (instead of long polling) for higher throughput
