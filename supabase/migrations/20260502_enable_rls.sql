-- Enable RLS on receipts-bot tables. Service-role key bypasses RLS,
-- so the bot keeps working; this protects against anon-key leaks.
alter table if exists public.trips    enable row level security;
alter table if exists public.receipts enable row level security;
