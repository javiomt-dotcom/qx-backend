"""Almacenamiento: Supabase (Postgres vía su API REST) en producción, SQLite local para pruebas.

Las dos clases exponen lo mismo: upsert(tabla, filas, conflicto) y select(tabla, filtros).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import requests

from . import config as C

PK = {
    "symbols": ["symbol"], "bars": ["symbol", "d"], "forecasts": ["signal_date"],
    "picks": ["signal_date", "symbol"], "outcomes": ["signal_date", "symbol"],
    "daily_results": ["signal_date"],
    "swing_picks": ["signal_date", "symbol"], "swing_results": ["session"],
    "earnings": ["symbol", "report_date"],
    "alerts": ["day", "symbol", "kind"],
    "news_daily": ["d", "symbol"], "news_market": ["d"],
}
JSON_COLS = {"meta", "reasons", "detail", "headlines", "themes"}


class SupabaseStore:
    def __init__(self, url=None, key=None, session=None):
        self.url = (url or C.SUPABASE_URL) + "/rest/v1"
        k = key or C.SUPABASE_KEY
        self.h = {"apikey": k, "Content-Type": "application/json"}
        if not k.startswith("sb_"):          # llaves viejas (JWT); las nuevas sb_secret_ van sólo en apikey
            self.h["Authorization"] = f"Bearer {k}"
        self.s = session or requests.Session()

    def upsert(self, table, rows, batch=2000):
        for i in range(0, len(rows), batch):
            r = self.s.post(f"{self.url}/{table}", headers={**self.h, "Prefer": "resolution=merge-duplicates,return=minimal"},
                            params={"on_conflict": ",".join(PK[table])}, data=json.dumps(rows[i:i + batch]), timeout=120)
            r.raise_for_status()

    def delete(self, table, filters):
        r = self.s.delete(f"{self.url}/{table}", headers={**self.h, "Prefer": "return=minimal"}, params=filters, timeout=60)
        r.raise_for_status()

    def select(self, table, filters=None, order=None, limit=None):
        params = dict(filters or {})
        params["select"] = "*"
        if order:
            params["order"] = order
        out, off, page = [], 0, 1000
        while True:
            h = {**self.h, "Range-Unit": "items", "Range": f"{off}-{off + page - 1}"}
            r = self.s.get(f"{self.url}/{table}", headers=h, params=params, timeout=120)
            r.raise_for_status()
            rows = r.json()
            out += rows
            if len(rows) < page or (limit and len(out) >= limit):
                return out[:limit] if limit else out
            off += page

    def log_run(self, kind, status, detail):
        r = self.s.post(f"{self.url}/runs", headers={**self.h, "Prefer": "return=minimal"},
                        data=json.dumps({"kind": kind, "status": status, "detail": detail,
                                         "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()}), timeout=60)
        r.raise_for_status()


class LocalStore:
    """SQLite con el mismo esquema (simplificado) para pruebas y para correr sin Supabase."""
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        cols = {
            "symbols": "symbol, name, exchange, sp500, first_seen",
            "bars": "symbol, d, o, h, l, c, v",
            "forecasts": "signal_date, for_session, created_at, model, regime, universe, meta",
            "picks": "signal_date, rank, symbol, p_raw, p_cal, close, atr, max_entry, stop, target, shares, sp500, reasons",
            "outcomes": "signal_date, symbol, session, open, high, low, close, cc, ret, flag, in_top",
            "daily_results": "signal_date, session, model_ret, universe_oc, spy_oc, hits, skipped, stops",
            "runs": "kind, status, detail",
            "swing_picks": "signal_date, rank, symbol, score, close, atr, max_entry, shares, sp500, reasons, sell_session",
            "swing_results": "session, ret, spy_cc, cohorts",
            "earnings": "symbol, report_date, time",
            "alerts": "day, symbol, kind, detail",
            "news_daily": "d, symbol, n, tone",
            "news_market": "d, n, tone, spy_ret, headlines, themes",
        }
        for t, c in cols.items():
            pk = f", primary key ({', '.join(PK[t])})" if t in PK else ""
            self.db.execute(f"create table if not exists {t} ({c}{pk})")

    def upsert(self, table, rows, batch=None):
        for r in rows:
            r = {k: (json.dumps(v) if k in JSON_COLS else v) for k, v in r.items()}
            keys = list(r)
            upd = ", ".join(f"{k}=excluded.{k}" for k in keys if k not in PK[table])
            sql = f"insert into {table} ({', '.join(keys)}) values ({', '.join('?' * len(keys))})"
            sql += f" on conflict ({', '.join(PK[table])}) do update set {upd}" if upd else " on conflict do nothing"
            self.db.execute(sql, [r[k] for k in keys])
        self.db.commit()

    def delete(self, table, filters):
        where = [f"{k} = ?" for k in filters]
        self.db.execute(f"delete from {table} where {' and '.join(where)}", [v.split(".", 1)[1] for v in filters.values()])
        self.db.commit()

    def select(self, table, filters=None, order=None, limit=None):
        where, args = [], []
        for k, v in (filters or {}).items():
            op, val = v.split(".", 1)
            sym = {"eq": "=", "gte": ">=", "lte": "<=", "lt": "<", "gt": ">"}[op]
            where.append(f"{k} {sym} ?"); args.append(val)
        sql = f"select * from {table}" + (f" where {' and '.join(where)}" if where else "")
        if order:
            col, _, d = order.partition(".")
            sql += f" order by {col} {'desc' if d == 'desc' else 'asc'}"
        if limit:
            sql += f" limit {int(limit)}"
        out = []
        for row in self.db.execute(sql, args):
            r = dict(row)
            for k in JSON_COLS & r.keys():
                if isinstance(r[k], str):
                    r[k] = json.loads(r[k])
            out.append(r)
        return out

    def log_run(self, kind, status, detail):
        self.db.execute("insert into runs (kind, status, detail) values (?, ?, ?)", (kind, status, json.dumps(detail)))
        self.db.commit()
