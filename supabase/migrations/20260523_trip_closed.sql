-- Mark a trip as closed (final report generated). Closed trips no longer
-- auto-attach new receipts and stop prompting to close.
alter table trips add column if not exists closed_at timestamptz null;
