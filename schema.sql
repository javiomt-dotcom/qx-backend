-- Esquema de la base de datos de QX (Postgres / Supabase).
-- Se corre una sola vez en el editor SQL de Supabase.

create table if not exists symbols (
  symbol      text primary key,
  name        text,
  exchange    text,
  sp500       boolean not null default false,
  first_seen  date not null default current_date
);

-- velas diarias definitivas (nunca la vela de una sesión en curso)
create table if not exists bars (
  symbol  text not null,
  d       date not null,
  o       double precision,
  h       double precision,
  l       double precision,
  c       double precision,
  v       double precision,
  primary key (symbol, d)
);
create index if not exists bars_d_idx on bars (d);

-- un pronóstico por fecha de señal (cierre del día d, para operar la siguiente sesión)
create table if not exists forecasts (
  signal_date  date primary key,
  for_session  date not null,
  created_at   timestamptz not null default now(),
  model        text not null,
  regime       text not null,
  universe     int  not null,
  meta         jsonb not null default '{}'::jsonb
);

create table if not exists picks (
  signal_date  date not null references forecasts(signal_date) on delete cascade,
  rank         int  not null,
  symbol       text not null,
  p_raw        double precision not null,
  p_cal        double precision not null,
  close        double precision not null,
  atr          double precision not null,
  max_entry    double precision not null,   -- no comprar si abre arriba de esto
  stop         double precision not null,   -- calculado desde el cierre
  target       double precision not null,
  shares       int,
  sp500        boolean not null,
  reasons      jsonb not null default '[]'::jsonb,
  primary key (signal_date, symbol)
);

-- resultado real de cada pick con las reglas realistas (compra en la apertura)
create table if not exists outcomes (
  signal_date  date not null,
  symbol       text not null,
  session      date not null,
  open         double precision,
  high         double precision,
  low          double precision,
  close        double precision,
  cc           double precision,     -- cierre a cierre
  ret          double precision,     -- realista
  flag         text not null,        -- OK | STOP | TARGET | SKIPPED | NODATA
  in_top       boolean,              -- terminó entre los ganadores del universo
  primary key (signal_date, symbol),
  foreign key (signal_date, symbol) references picks(signal_date, symbol) on delete cascade
);

create table if not exists daily_results (
  signal_date  date primary key references forecasts(signal_date) on delete cascade,
  session      date not null,
  model_ret    double precision,
  universe_oc  double precision,
  spy_oc       double precision,
  hits         int,
  skipped      int,
  stops        int
);

create table if not exists runs (
  id          bigserial primary key,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  kind        text not null,
  status      text not null default 'running',
  detail      jsonb not null default '{}'::jsonb
);
