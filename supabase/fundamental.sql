-- Tablero fundamental (docs/fundamental/), aparte del motor de trading. Aplicado en Supabase el 2026-11-13.
create table if not exists public.fa_cache (
  key text primary key,
  data jsonb not null,
  fetched_at timestamptz not null default now()
);
alter table public.fa_cache enable row level security;  -- sin políticas: sólo la función (service role)

create table if not exists public.fa_positions (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  symbol text not null check (symbol ~ '^[A-Z][A-Z0-9.\-]{0,9}$'),
  shares numeric not null check (shares > 0),
  cost numeric check (cost is null or cost > 0),
  bought_on date,
  note text,
  created_at timestamptz not null default now()
);
create index if not exists fa_positions_user on public.fa_positions(user_id);
alter table public.fa_positions enable row level security;
create policy fa_positions_own on public.fa_positions for all to authenticated
  using (user_id = auth.uid() and public.qx_allowed())
  with check (user_id = auth.uid() and public.qx_allowed());

create table if not exists public.fa_watchlist (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  symbol text not null check (symbol ~ '^[A-Z][A-Z0-9.\-]{0,9}$'),
  target numeric check (target is null or target > 0),
  note text,
  added_at timestamptz not null default now(),
  unique (user_id, symbol)
);
alter table public.fa_watchlist enable row level security;
create policy fa_watchlist_own on public.fa_watchlist for all to authenticated
  using (user_id = auth.uid() and public.qx_allowed())
  with check (user_id = auth.uid() and public.qx_allowed());

create table if not exists public.fa_settings (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  profile text not null default 'moderado' check (profile in ('conservador','moderado_conservador','moderado','crecimiento','agresivo')),
  cash numeric not null default 0 check (cash >= 0),
  updated_at timestamptz not null default now()
);
alter table public.fa_settings enable row level security;
create policy fa_settings_own on public.fa_settings for all to authenticated
  using (user_id = auth.uid() and public.qx_allowed())
  with check (user_id = auth.uid() and public.qx_allowed());

grant select, insert, update, delete on public.fa_positions, public.fa_watchlist, public.fa_settings to authenticated;
revoke all on public.fa_cache from anon, authenticated;
