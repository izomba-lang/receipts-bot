create table if not exists trips (
  id          bigserial primary key,
  user_id     text not null,
  name        text not null,
  start_date  date not null,
  end_date    date not null,
  notes       text null,
  created_at  timestamptz not null default now()
);

create index if not exists trips_user_dates_idx on trips (user_id, start_date, end_date);

alter table receipts
  add column if not exists trip_id bigint null references trips(id) on delete set null;

create index if not exists receipts_trip_idx on receipts (trip_id) where trip_id is not null;
