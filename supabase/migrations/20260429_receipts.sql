create table if not exists receipts (
  id            bigserial primary key,
  user_id       text not null,
  date          date not null,
  time          time null,
  provider      text null,
  category      text not null default 'other',
  amount        numeric(14,2) not null,
  currency      text not null,
  from_location text null,
  to_location   text null,
  receipt_number text null,
  trip_number    text null,
  payment_method text null,
  source_kind   text not null,
  source_file_id text null,
  raw_text      text null,
  confidence    numeric(4,3) null,
  status        text not null default 'confirmed',
  notes         text null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null
);

create index if not exists receipts_user_date_idx on receipts (user_id, date);
create index if not exists receipts_status_idx on receipts (status) where status != 'deleted';
