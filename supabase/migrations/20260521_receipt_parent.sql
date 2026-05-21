-- Link a receipt as a supporting attachment of another (e.g. bill + fiscal receipt
-- for one payment). Attachments keep their file but are excluded from report totals.
alter table receipts
  add column if not exists parent_id bigint null references receipts(id) on delete set null;

create index if not exists receipts_parent_idx on receipts (parent_id) where parent_id is not null;
