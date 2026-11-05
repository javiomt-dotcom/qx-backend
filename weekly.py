"""Reporte semanal de QX: corre los viernes después de la rutina diaria y lo manda por Telegram.

Compara los resultados reales contra el SPY:
- Swing 5 días: la semana día por día, las listas que se vendieron en la semana (con la mejor y la peor)
  y el acumulado desde el arranque, con cuántas semanas le ganó al SPY.
- Modelo de 1 día (sólo comparación): la semana y el acumulado.

Uso:  python weekly.py            (producción: Supabase + Alpaca + Telegram)
      python weekly.py --force    (lo manda aunque ya se haya mandado esta semana)
      python weekly.py --dry      (sólo imprime el mensaje, no lo manda ni lo marca)

El viernes, si el resultado del día todavía no está (la rutina diaria no ha terminado), no manda nada
y el respaldo del sábado lo manda con lo que haya.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import traceback
from zoneinfo import ZoneInfo

import numpy as np

from qx import notify, swing as SW
from qx.notify import pct

NY = ZoneInfo("America/New_York")
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie"]


def compound(xs):
    return float(np.prod([1 + x for x in xs]) - 1) if xs else None


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def weekly_table(rows, key_m, key_s):
    """{lunes: (retorno modelo, retorno SPY, días)} agrupando sesiones por semana."""
    by = {}
    for r in rows:
        w = monday(dt.date.fromisoformat(str(r["session"])[:10])).isoformat()
        by.setdefault(w, []).append(r)
    return {w: (compound([r[key_m] for r in rs]), compound([r[key_s] for r in rs]), len(rs)) for w, rs in sorted(by.items())}


def closed_lists(store, a, week_sessions, cal):
    """Listas swing que se vendieron esta semana: retorno real de apertura de compra a cierre de venta."""
    if not week_sessions:
        return []
    rows = store.select("swing_picks", {"sell_session": f"gte.{week_sessions[0]}"})
    rows = [r for r in rows if r.get("sell_session") and str(r["sell_session"])[:10] <= week_sessions[-1]]
    if not rows:
        return []
    by = {}
    for r in rows:
        by.setdefault(str(r["signal_date"])[:10], []).append(r)
    nxt = lambda d: cal[cal.index(d) + 1] if d in cal and cal.index(d) + 1 < len(cal) else None
    start = dt.date.fromisoformat(min(by))
    bars = a.daily_bars(sorted({r["symbol"] for r in rows}), start)
    out = []
    for sig, rs in sorted(by.items()):
        buy, sell = nxt(sig), str(rs[0]["sell_session"])[:10]
        got, skipped = [], 0
        for r in rs:
            B = bars.get(r["symbol"])
            if not B or buy not in B["t"] or sell not in B["t"]:
                continue
            o = B["o"][B["t"].index(buy)]
            if r.get("max_entry") is not None and o > float(r["max_entry"]):
                skipped += 1                        # abrió arriba de "No comprar arriba de": no se compró
                got.append((r["symbol"], None))
                continue
            got.append((r["symbol"], B["c"][B["t"].index(sell)] / o - 1 - SW.COST))
        bought = [(s, x) for s, x in got if x is not None]
        if not got:
            continue
        avg = float(np.mean([x if x is not None else 0.0 for _, x in got]))
        best = max(bought, key=lambda t: t[1]) if bought else None
        worst = min(bought, key=lambda t: t[1]) if bought else None
        out.append({"signal": sig, "buy": buy, "sell": sell, "ret": avg, "n": len(got), "skipped": skipped,
                    "best": best, "worst": worst})
    return out


def build(store, a, today: dt.date):
    """Arma el reporte de la semana de `today`. Devuelve dict con el mensaje y si la semana ya está completa."""
    mon = monday(today)
    fri = mon + dt.timedelta(days=4)
    cal = [r["date"] for r in a.calendar(mon - dt.timedelta(days=21), fri + dt.timedelta(days=10))]
    week = [d for d in cal if mon.isoformat() <= d <= min(fri, today).isoformat()]

    sw = [r for r in store.select("swing_results", order="session.asc") if r.get("ret") is not None and r.get("spy_cc") is not None]
    d1 = [r for r in store.select("daily_results", order="session.asc") if r.get("model_ret") is not None and r.get("spy_oc") is not None]
    sw_w = [r for r in sw if str(r["session"])[:10] in week]
    d1_w = [r for r in d1 if str(r["session"])[:10] in week]
    complete = bool(week) and any(str(r["session"])[:10] == week[-1] for r in sw_w)

    L = [f"<b>📊 QX · Resumen semanal</b>",
         f"Semana del {mon.strftime('%d-%m')} al {fri.strftime('%d-%m-%Y')} · {len(week)} sesiones"]
    if not week:
        L.append("Esta semana no hubo sesiones de mercado.")
        return {"text": "\n".join(L), "complete": True, "week": mon.isoformat()}

    # --- swing: la semana ---
    L.append("")
    L.append("<b>Swing 5 días (el que usas)</b>")
    if sw_w:
        m, s = compound([r["ret"] for r in sw_w]), compound([r["spy_cc"] for r in sw_w])
        beat = sum(1 for r in sw_w if r["ret"] > r["spy_cc"])
        verdict = "le ganó al SPY ✅" if m > s else ("empató con el SPY" if m == s else "perdió contra el SPY ❌")
        L.append(f"Semana: swing {pct(m)} vs SPY {pct(s)} · {verdict} por {abs(m - s) * 100:.2f} puntos · "
                 f"mejor que el SPY {beat} de {len(sw_w)} días")
        rows = ["Día        Swing      SPY"]
        for r in sw_w:
            d = dt.date.fromisoformat(str(r["session"])[:10])
            rows.append(f"{DIAS[d.weekday()]} {d.strftime('%d-%m')}  {pct(r['ret']):>8} {pct(r['spy_cc']):>8}")
        L.append("<pre>" + "\n".join(rows) + "</pre>")
        if any((r.get("cohorts") or SW.HOLD) < SW.HOLD for r in sw_w):
            L.append("ℹ️ Algunos días tenían menos de 5 listas abiertas (arranque), así que parte del capital estaba en efectivo.")
    else:
        L.append("Todavía no hay sesiones del swing calificadas esta semana.")
    if not complete:
        L.append(f"⚠️ Falta el resultado del {week[-1]}: la rutina diaria todavía no lo calificaba.")

    try:
        closed = closed_lists(store, a, week, cal)
    except Exception as e:
        traceback.print_exc()
        closed, L = [], L + [f"⚠️ No pude calcular las listas vendidas ({type(e).__name__})."]
    if closed:
        L.append("<b>Listas que se vendieron esta semana</b> (de la apertura de compra al cierre de venta):")
        for c in closed:
            extra = []
            if c["best"]:
                extra.append(f"mejor {c['best'][0]} {pct(c['best'][1], 1)}")
            if c["worst"]:
                extra.append(f"peor {c['worst'][0]} {pct(c['worst'][1], 1)}")
            if c["skipped"]:
                extra.append(f"{c['skipped']} no se compraron (abrieron arriba del límite)")
            L.append(f"• Señal {c['signal'][5:]} (vendida el {c['sell'][5:]}): {pct(c['ret'])}" + (" · " + " · ".join(extra) if extra else ""))

    # --- swing: acumulado ---
    if sw:
        m, s = compound([r["ret"] for r in sw]), compound([r["spy_cc"] for r in sw])
        wk = weekly_table(sw, "ret", "spy_cc")
        won = sum(1 for x in wk.values() if x[0] > x[1])
        L.append(f"<b>Acumulado real desde el {str(sw[0]['session'])[:10]}</b> ({len(sw)} sesiones): "
                 f"swing {pct(m, 1)} vs SPY {pct(s, 1)} · diferencia {(m - s) * 100:+.1f} puntos · "
                 f"le ganó {won} de {len(wk)} semanas")

    # --- modelo de 1 día ---
    L.append("")
    L.append("<b>Modelo 1 día (sólo comparación)</b>")
    if d1_w:
        L.append(f"Semana: {pct(compound([r['model_ret'] for r in d1_w]))} vs SPY {pct(compound([r['spy_oc'] for r in d1_w]))} "
                 f"· mejor que el SPY {sum(1 for r in d1_w if r['model_ret'] > r['spy_oc'])} de {len(d1_w)} días")
    else:
        L.append("Semana: sin sesiones calificadas.")
    if d1:
        L.append(f"Acumulado ({len(d1)} sesiones): {pct(compound([r['model_ret'] for r in d1]), 1)} "
                 f"vs SPY {pct(compound([r['spy_oc'] for r in d1]), 1)}")

    L.append("")
    L.append("Con pocas semanas el resultado todavía es muy ruidoso; lo importante es la tendencia de varias semanas. "
             "El SPY del swing es de cierre a cierre y el del modelo de 1 día de apertura a cierre. No es asesoría financiera.")
    return {"text": "\n".join(L), "complete": complete, "week": mon.isoformat()}


def already_sent(store, week: str) -> bool:
    try:
        rows = store.select("runs", {"kind": "eq.weekly"})
    except Exception as e:
        print("runs select:", e)
        return False
    return any(r.get("status") == "ok" and (r.get("detail") or {}).get("week") == week for r in rows)


def run(store, a, now_et=None, send=notify.send, force=False, dry=False):
    now_et = now_et or dt.datetime.now(NY)
    today = now_et.date()
    if today.weekday() == 6:                 # domingo: la semana que acaba de terminar
        today -= dt.timedelta(days=2)
    rep = build(store, a, today)
    if dry:
        print(rep["text"])
        return rep
    if not force and already_sent(store, rep["week"]):
        print(f"El reporte de la semana {rep['week']} ya se mandó.")
        return None
    if not force and not rep["complete"] and today.weekday() == 4:
        print("Falta el resultado de hoy; lo manda el respaldo del sábado.")
        return None
    send(rep["text"])
    store.log_run("weekly", "ok", {"week": rep["week"], "complete": rep["complete"]})
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--local", action="store_true")
    args = ap.parse_args()
    from qx.data_alpaca import Alpaca
    from qx.store import LocalStore, SupabaseStore
    store = LocalStore("qx.sqlite") if args.local else SupabaseStore()
    try:
        run(store, Alpaca(), force=args.force, dry=args.dry)
    except Exception as e:
        traceback.print_exc()
        try:
            notify.send(f"⚠️ QX: el reporte semanal falló: {type(e).__name__}: {e}")
        except Exception as te:
            print(f"No se pudo avisar por Telegram: {te}")
        sys.exit(1)


if __name__ == "__main__":
    main()
