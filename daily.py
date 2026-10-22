"""Rutina diaria de QX: corre sola después del cierre.

1. Actualiza precios (Alpaca) del universo: S&P 500 + acciones líquidas de todo el mercado.
2. Guarda las velas nuevas en la base de datos.
3. Califica con las reglas realistas los pronósticos cuya sesión ya cerró.
4. Corre el modelo y guarda la lista para la siguiente sesión.
5. Manda el resumen por Telegram.

Uso:  python daily.py            (producción: Alpaca + Supabase + Telegram)
      python daily.py --local    (SQLite local en qx.sqlite, sin Supabase)
      python daily.py --force    (rehace la lista aunque ya exista)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from zoneinfo import ZoneInfo

import numpy as np

from qx import config as C, engine as E, notify
from qx.data_alpaca import Alpaca, last_completed_session
from qx.sp500 import SP500, INDEXES

NY = ZoneInfo("America/New_York")


def pick_universe(scan: dict, sp500=SP500):
    out = []
    for s, r in scan.items():
        if s in INDEXES:
            continue
        c, v = np.array(r["c"], float), np.array(r["v"], float)
        if not len(c):
            continue
        if s in sp500:
            out.append(s)
            continue
        dv = np.nanmean((c * v)[-20:])
        if c[-1] >= C.MIN_PRICE and dv >= C.MIN_DOLLAR_VOL:
            out.append(s)
    return sorted(out)


def cut(bars: dict, last: str):
    """Quita cualquier vela posterior a la última sesión cerrada (la de hoy, si aún no es definitiva)."""
    for r in bars.values():
        n = sum(1 for t in r["t"] if t <= last)
        for k in r:
            r[k] = r[k][:n]
    return {s: r for s, r in bars.items() if r["t"]}


def score_forecast(panel: E.Panel, f: dict, picks: list[dict], model_syms: list[str]):
    """Resultado real de una lista: compra en la apertura con las reglas de la terminal."""
    if f["for_session"] not in panel.dates or f["signal_date"] not in panel.dates:
        return None, []
    i0, i1 = panel.dates.index(f["signal_date"]), panel.dates.index(f["for_session"])
    idx = panel.idx
    rows = np.array([idx[s] for s in model_syms if s in idx])
    with np.errstate(invalid="ignore", divide="ignore"):
        rets = panel.c[rows, i1] / panel.c[rows, i0] - 1
        oc = panel.c[rows, i1] / panel.o[rows, i1] - 1
    fin = np.isfinite(rets)
    topn = E.topn_for(int(fin.sum()))
    top_syms = {panel.syms[rows[k]] for k in np.argsort(-np.where(fin, rets, -np.inf))[:topn] if fin[k]}
    outs, rs = [], []
    for p in picks:
        r = idx.get(p["symbol"])
        if r is None:
            outs.append({"signal_date": f["signal_date"], "symbol": p["symbol"], "session": f["for_session"], "flag": "NODATA"})
            continue
        o1, h1, l1, c1, c0 = (float(panel.o[r, i1]), float(panel.h[r, i1]), float(panel.l[r, i1]),
                              float(panel.c[r, i1]), float(panel.c[r, i0]))
        ret, flag = E.trade_at(o1, h1, l1, c1, c0, float(p["atr"]))
        rs.append(0.0 if flag == "SKIPPED" else ret)
        outs.append({"signal_date": f["signal_date"], "symbol": p["symbol"], "session": f["for_session"],
                     "open": o1, "high": h1, "low": l1, "close": c1, "cc": c1 / c0 - 1 if c0 else None,
                     "ret": None if flag == "SKIPPED" else ret, "flag": flag, "in_top": p["symbol"] in top_syms})
    spy = idx.get("SPY")
    spy_oc = float(panel.c[spy, i1] / panel.o[spy, i1] - 1) if spy is not None else None
    rs = [x for x in rs if x == x]
    res = {"signal_date": f["signal_date"], "session": f["for_session"],
           "model_ret": float(np.mean(rs)) if rs else None, "universe_oc": float(np.nanmean(oc)),
           "spy_oc": spy_oc, "hits": sum(1 for o in outs if o.get("in_top")),
           "skipped": sum(1 for o in outs if o["flag"] == "SKIPPED"), "stops": sum(1 for o in outs if o["flag"] == "STOP")}
    return res, outs


def cumulative(results: list[dict]):
    rs = [r for r in results if r.get("model_ret") is not None and r.get("spy_oc") is not None]
    m = s = 1.0
    for r in rs:
        m *= 1 + r["model_ret"]; s *= 1 + r["spy_oc"]
    return {"n": len(rs), "model": m - 1, "spy": s - 1, "beat": sum(1 for r in rs if r["model_ret"] > r["spy_oc"])}


def run(alpaca, store, now_et=None, send=notify.send, force=False):
    now_et = now_et or dt.datetime.now(NY)
    today = now_et.date()
    cal = alpaca.calendar(today - dt.timedelta(days=40), today + dt.timedelta(days=10))
    last = last_completed_session(cal, now_et)
    nxt = next(r["date"] for r in cal if r["date"] > last)
    if not force and store.select("forecasts", {"signal_date": f"eq.{last}"}):
        print(f"Ya existe la lista con señal {last}; nada que hacer.")
        return None

    assets = alpaca.assets()
    names = {a["symbol"]: a for a in assets}
    cands = sorted(set(names) | set(SP500) | set(INDEXES))
    scan = cut(alpaca.daily_bars(cands, today - dt.timedelta(days=35)), last)
    universe = pick_universe(scan)
    bars = cut(alpaca.daily_bars(universe + list(INDEXES), today - dt.timedelta(days=C.HISTORY_DAYS)), last)
    if "SPY" not in bars or bars["SPY"]["t"][-1] != last:
        raise RuntimeError(f"Alpaca todavía no tiene el cierre del {last} para el SPY")

    # --- base de datos: símbolos nuevos con todo su historial, conocidos sólo los últimos 10 días ---
    known = {r["symbol"] for r in store.select("symbols")}
    store.upsert("symbols", [{"symbol": s, "name": names.get(s, {}).get("name", ""), "exchange": names.get(s, {}).get("exchange", ""),
                              "sp500": s in SP500} for s in bars if s not in known])
    rows = []
    for s, r in bars.items():
        k0 = 0 if s not in known else max(0, len(r["t"]) - 10)
        rows += [{"symbol": s, "d": r["t"][k], "o": r["o"][k], "h": r["h"][k], "l": r["l"][k], "c": r["c"][k], "v": r["v"][k]}
                 for k in range(k0, len(r["t"]))]
    store.upsert("bars", rows)

    panel = E.Panel.from_bars(bars)
    model_syms = [s for s in universe if s in bars and len(bars[s]["t"]) > 61]

    # --- calificar las listas cuya sesión ya cerró ---
    done = {r["signal_date"] for r in store.select("daily_results")}
    y = None
    for f in store.select("forecasts", {"for_session": f"lte.{last}"}, order="signal_date.asc"):
        if f["signal_date"] in done:
            continue
        pk = store.select("picks", {"signal_date": f"eq.{f['signal_date']}"})
        res, outs = score_forecast(panel, f, pk, model_syms)
        if res:
            store.upsert("outcomes", outs)
            store.upsert("daily_results", [res])
            y = res

    # --- modelo ---
    logged = {}
    for p in store.select("picks"):
        logged.setdefault(p["signal_date"], []).append({"s": p["symbol"], "p": p["p_raw"]})
    res = E.run(panel, model_syms, [{"date": d, "picks": v} for d, v in logged.items()])
    if res.last.date != last:
        raise RuntimeError(f"El último día con datos suficientes es {res.last.date}, no {last}")
    reg = res.last.regime["key"]
    sel = E.select_picks(res, panel, SP500)
    f = {"signal_date": last, "for_session": nxt, "model": C.MODEL_NAME, "regime": reg, "universe": len(res.last.rows),
         "meta": {"topn": res.topn, "n_real": res.n_real, "cal": res.cal, "regime": res.last.regime,
                  "backtest": {"n": len(res.bt), "model": float(np.prod([1 + b["picks"] for b in res.bt]) - 1),
                               "universe": float(np.prod([1 + b["uni"] for b in res.bt if b["uni"] == b["uni"]]) - 1)}}}
    picks = []
    for k, x in enumerate(sel, 1):
        pl = E.plan(panel, res.last.i, x, reg)
        picks.append({"signal_date": last, "rank": k, "symbol": x["s"], "p_raw": x["p_raw"], "p_cal": x["p_cal"],
                      "sp500": x["s"] in SP500, "reasons": x["reasons"], **pl})
    store.upsert("forecasts", [f])
    store.upsert("picks", picks)

    cum = cumulative(store.select("daily_results", order="signal_date.asc"))
    msg = notify.daily_message(f, picks, y, cum)
    send(msg)
    store.log_run("daily", "ok", {"signal": last, "universe": f["universe"], "picks": [p["symbol"] for p in picks]})
    return {"forecast": f, "picks": picks, "yesterday": y, "cum": cum, "message": msg}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    from qx.store import LocalStore, SupabaseStore
    store = LocalStore("qx.sqlite") if a.local else SupabaseStore()
    try:
        run(Alpaca(), store, force=a.force)
    except Exception as e:
        traceback.print_exc()
        notify.send(f"⚠️ QX: la rutina diaria falló: {type(e).__name__}: {e}")
        try:
            store.log_run("daily", "error", {"error": str(e)})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
