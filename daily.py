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
import os
import datetime as dt
import sys
import traceback
from zoneinfo import ZoneInfo

import numpy as np

from qx import config as C, earnings as EA, engine as E, news as NW, notify, swing as SW
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


def run_swing(panel, universe, store, last, sessions):
    """Lista swing del día, calificación de las sesiones cerradas y resumen para el mensaje."""
    out = {"picks": [], "sells": [], "yesterday": None, "cum": None, "error": None}
    try:
        # calificar sesiones cerradas que todavía no tienen resultado
        prev = store.select("swing_picks", {"signal_date": f"gte.{sessions[0]}"})
        cohorts = {}
        for r in prev:
            cohorts.setdefault(str(r["signal_date"]), []).append(r["symbol"])
        done = {str(r["session"]) for r in store.select("swing_results")}
        if cohorts:
            first = min(cohorts)
            todo = [d for d in sessions if first < d <= last and d not in done]
            res = [x for x in (SW.score_session(panel, d, cohorts, sessions) for d in todo) if x]
            if res:
                store.upsert("swing_results", res)
                out["yesterday"] = res[-1]
        allr = store.select("swing_results", order="session.asc")
        rs = [r for r in allr if r.get("ret") is not None and r.get("spy_cc") is not None]
        if rs:
            m = float(np.prod([1 + r["ret"] for r in rs]) - 1); s = float(np.prod([1 + r["spy_cc"] for r in rs]) - 1)
            out["cum"] = {"n": len(rs), "model": m, "spy": s}
        # lista nueva, sin las que reportan resultados mientras estarían en cartera
        i = len(panel.dates) - 1
        pos = sessions.index(last)
        sell = sessions[pos + HOLD_SELL] if pos + HOLD_SELL < len(sessions) else None
        earn, out["earn_note"] = load_earnings(store, last)
        end = sell or str(dt.date.fromisoformat(last) + dt.timedelta(days=8))
        # un reporte antes de la apertura del día de la señal ya está en el precio
        earn = [r for r in earn if not (str(r["report_date"])[:10] == last and r.get("time") == "pre-market")]
        skip = EA.in_window(earn, last, end)
        picks, info = SW.run(panel, universe, SP500, exclude=frozenset(skip))
        out["earn_skipped"] = [f"{s} ({skip[s][5:]})" for s in info["skipped"]]
        rows = []
        for k, x in enumerate(picks, 1):
            pl = SW.plan(panel, i, x)
            rows.append({"signal_date": last, "rank": k, "symbol": x["s"], "score": x["score"], "sp500": x["sp"],
                         "reasons": x["reasons"], "sell_session": sell, **pl})
        store.delete("swing_picks", {"signal_date": f"eq.{last}"})   # al rehacer la lista no quedan restos de la anterior
        store.upsert("swing_picks", rows)
        out["picks"] = rows
        # las que se venden al cierre de la próxima sesión: compradas con la señal de hace 4 sesiones
        sig_sell = sessions[pos - (SW.HOLD - 1)] if pos - (SW.HOLD - 1) >= 0 else None
        out["sells"] = cohorts.get(sig_sell, []) if sig_sell else []
        out["sell_signal"] = sig_sell
    except Exception as e:                      # el swing no debe tumbar la rutina principal
        import traceback
        traceback.print_exc()
        out["error"] = f"{type(e).__name__}: {e}"
    return out


HOLD_SELL = SW.HOLD        # la lista con señal k se vende al cierre de la sesión k+5


def load_earnings(store, last):
    """Baja el calendario de reportes (si hay llave de Alpha Vantage) y devuelve lo guardado desde la señal."""
    note = None
    if C.ALPHAVANTAGE_KEY:
        try:
            rows = EA.fetch()
            if rows:
                store.upsert("earnings", rows)
        except Exception as e:
            note = f"no se pudo bajar el calendario de reportes ({type(e).__name__}); uso el último guardado"
            print("earnings:", e)
    try:
        rows = store.select("earnings", {"report_date": f"gte.{last}"})
    except Exception as e:
        print("earnings select:", e)
        rows = []
    if not rows and not note:
        note = "sin calendario de reportes (falta el secreto ALPHAVANTAGE_KEY)" if not C.ALPHAVANTAGE_KEY else "el calendario de reportes llegó vacío"
    return rows, note


def cumulative(results: list[dict]):
    rs = [r for r in results if r.get("model_ret") is not None and r.get("spy_oc") is not None]
    m = s = 1.0
    for r in rs:
        m *= 1 + r["model_ret"]; s *= 1 + r["spy_oc"]
    return {"n": len(rs), "model": m - 1, "spy": s - 1, "beat": sum(1 for r in rs if r["model_ret"] > r["spy_oc"])}


def missing_secrets():
    need = {"ALPACA_KEY_ID": C.ALPACA_KEY, "ALPACA_SECRET_KEY": C.ALPACA_SECRET, "SUPABASE_URL": C.SUPABASE_URL,
            "SUPABASE_SERVICE_KEY": C.SUPABASE_KEY, "TELEGRAM_BOT_TOKEN": C.TELEGRAM_TOKEN, "TELEGRAM_CHAT_ID": C.TELEGRAM_CHAT}
    return [k for k, v in need.items() if not v]


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
        k0 = max(0, len(r["t"]) - (C.STORE_SESSIONS if s not in known else 10))
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
    store.delete("picks", {"signal_date": f"eq.{last}"})
    store.upsert("picks", picks)

    # --- swing de 5 días (en paralelo) ---
    sessions = [r["date"] for r in cal]
    sw_res = run_swing(panel, universe, store, last, sessions)

    # --- noticias: nunca deben tumbar la rutina ---
    try:
        nw = NW.daily(alpaca, panel, last, sessions, store, universe)
    except Exception as e:                                # noqa: BLE001
        traceback.print_exc()
        nw = {"error": str(e)[:160]}

    cum = cumulative(store.select("daily_results", order="signal_date.asc"))
    msg = (notify.swing_message(sw_res, f["for_session"]) + "\n\n"
           + notify.news_message(nw, [p["symbol"] for p in sw_res.get("picks", [])]) + "\n\n"
           + notify.daily_message(f, picks, y, cum))
    send(msg)
    store.log_run("daily", "ok", {"signal": last, "universe": f["universe"], "picks": [p["symbol"] for p in picks]})
    return {"forecast": f, "picks": picks, "yesterday": y, "cum": cum, "message": msg, "swing": sw_res, "news": nw}


def check():
    """Prueba cada conexión por separado y dice cuál falla."""
    import requests
    ok = True
    try:
        a = Alpaca()
        cal = a.calendar(dt.date.today() - dt.timedelta(days=7), dt.date.today())
        bars = a.daily_bars(["SPY"], dt.date.today() - dt.timedelta(days=7))
        print(f"✅ Alpaca: calendario {len(cal)} días, SPY último cierre {bars['SPY']['t'][-1]} = {bars['SPY']['c'][-1]}")
    except Exception as e:
        ok = False; print(f"❌ Alpaca: {e}")
    try:
        from qx.store import SupabaseStore
        st = SupabaseStore()
        n = len(st.select("forecasts", limit=1))
        try:
            st.log_run("check", "ok", {})
        except Exception as e:
            raise RuntimeError("lee pero no puede escribir: SUPABASE_SERVICE_KEY debe ser la llave service_role "
                               "(o la secret sb_secret_...), no la anon/publishable") from e
        print(f"✅ Supabase: conectado, lee y escribe ({n} listas guardadas)")
    except Exception as e:
        ok = False; print(f"❌ Supabase: {e}")
    try:
        me = requests.get(f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/getMe", timeout=30)
        if me.status_code != 200:
            t = C.TELEGRAM_TOKEN
            left, _, right = t.partition(":")
            raise RuntimeError(f"el token del bot no es válido (secreto {os.environ.get('QX_TOKEN_SRC', '?')}: "
                               f"{len(t)} caracteres, {'con' if ':' in t else 'SIN'} dos puntos, "
                               f"{len(left)} caracteres antes y {len(right)} después; "
                               f"{'solo números antes' if left.isdigit() else 'hay letras antes de los dos puntos'}; "
                               f"{'tiene espacios' if ' ' in t else 'sin espacios'})")
        up = requests.get(f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}/getUpdates", timeout=30).json().get("result", [])
        ids = {str(u[k]["chat"]["id"]) for u in up for k in ("message", "my_chat_member") if k in u}
        cid = C.TELEGRAM_CHAT
        print(f"Telegram: {len(ids)} chat(s) le han escrito al bot; TELEGRAM_CHAT_ID tiene {len(cid)} dígitos"
              f"{' y coincide' if cid in ids else (' y NO coincide con ninguno' if ids else '')}")
        if not ids:
            print("Telegram: no hay mensajes pendientes (el bot de comandos ya los leyó o nadie le ha escrito); "
                  "si no te llega el mensaje de prueba, mándale /start a tu bot")
        elif cid not in ids:
            raise RuntimeError("TELEGRAM_CHAT_ID no coincide con el chat que le escribió al bot; "
                               + ", ".join(f"ese chat termina en …{i[-3:]}" for i in ids))
        notify.send("✅ QX: conexión con Telegram lista. Aquí te llegará la lista diaria.")
        print(f"✅ Telegram: bot @{me.json()['result']['username']} y mensaje de prueba enviado")
    except Exception as e:
        ok = False; print(f"❌ Telegram: {e}")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        check()
    miss = missing_secrets()
    if miss and not a.local:
        print("Faltan estos secretos en GitHub (Settings > Secrets and variables > Actions): " + ", ".join(miss))
        sys.exit(1)
    from qx.store import LocalStore, SupabaseStore
    store = LocalStore("qx.sqlite") if a.local else SupabaseStore()
    try:
        run(Alpaca(), store, force=a.force)
    except Exception as e:
        traceback.print_exc()
        try:
            notify.send(f"⚠️ QX: la rutina diaria falló: {type(e).__name__}: {e}")
        except Exception as te:
            print(f"No se pudo avisar por Telegram: {te}")
        try:
            store.log_run("daily", "error", {"error": str(e)})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
