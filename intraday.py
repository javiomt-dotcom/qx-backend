"""Alertas durante la sesión para el swing de 5 días (corre cada 30 minutos con el mercado abierto).

- No comprar: una acción de la lista de hoy abrió arriba de su «No comprar arriba de».
- Caída: una posición abierta va más de 2 ATR abajo de su precio de compra (el swing no usa stop; es un aviso).
- Vender hoy: la última hora de la sesión, recuerda qué lista se vende al cierre.

Usa barras de Alpaca con 16 minutos de retraso (plan gratis). Cada alerta se manda una sola vez al día.
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback
from zoneinfo import ZoneInfo

from qx import config as C, notify, swing as SW
from qx.data_alpaca import Alpaca

NY = ZoneInfo("America/New_York")
DROP_ATR = 2.0
SELL_WINDOW_MIN = 60          # minutos antes del cierre para recordar la venta
OPEN_DELAY_MIN = 17           # la barra del día aparece 16 minutos después de la apertura


def run(a, store, now_et=None, send=notify.send):
    now_et = now_et or dt.datetime.now(NY)
    today = now_et.date()
    cal = a.calendar(today - dt.timedelta(days=14), today + dt.timedelta(days=3))
    days = [r["date"] for r in cal]
    t = today.isoformat()
    if t not in days:
        print("Hoy no hay mercado."); return None
    hh, mm = map(int, next(r["close"] for r in cal if r["date"] == t).split(":"))
    mins, close = now_et.hour * 60 + now_et.minute, hh * 60 + mm
    if mins < 9 * 60 + 30 + OPEN_DELAY_MIN or mins > close + 16:
        print("Fuera de horario de mercado."); return None
    pos = days.index(t)
    sigs = days[max(0, pos - SW.HOLD):pos]                     # listas con posiciones vivas hoy
    if not sigs:
        return None
    rows = [r for r in store.select("swing_picks", {"signal_date": f"gte.{sigs[0]}"}) if str(r["signal_date"]) in sigs]
    if not rows:
        print("No hay listas swing guardadas."); return None
    buy_of = {s: days[days.index(s) + 1] for s in sigs}
    bars = a.daily_bars(sorted({r["symbol"] for r in rows}), dt.date.fromisoformat(buy_of[sigs[0]]))
    done = {(r["symbol"], r["kind"]) for r in store.select("alerts", {"day": f"eq.{t}"})}
    new, sells = [], {}
    for r in sorted(rows, key=lambda r: (str(r["signal_date"]), r["rank"])):
        s, sig = r["symbol"], str(r["signal_date"])
        B = bars.get(s)
        if not B or t not in B["t"] or buy_of[sig] not in B["t"]:
            continue
        entry, px = B["o"][B["t"].index(buy_of[sig])], B["c"][B["t"].index(t)]
        if buy_of[sig] == t and entry > r["max_entry"]:
            new.append({"symbol": s, "kind": "no_buy",
                        "text": f"🚫 <b>{s}</b> abrió en {entry:.2f}, arriba de {r['max_entry']:.2f}. No la compres hoy."})
            continue
        if r.get("atr") and px <= entry - DROP_ATR * r["atr"]:
            new.append({"symbol": s, "kind": "drop",
                        "text": f"📉 <b>{s}</b> va {(px / entry - 1) * 100:+.1f}% desde la compra ({px:.2f}, entrada {entry:.2f}): "
                                f"más de {DROP_ATR:g} ATR abajo. El swing no usa stop; tú decides."})
        if str(r.get("sell_session")) == t:
            sells.setdefault(sig, []).append((s, px / entry - 1))
    if sells and mins >= close - SELL_WINDOW_MIN:
        for sig, xs in sells.items():
            avg = sum(x for _, x in xs) / len(xs)
            new.append({"symbol": sig, "kind": "sell",
                        "text": f"🔔 <b>Hoy al cierre vende</b> la lista del {sig}: {', '.join(s for s, _ in xs)} "
                                f"(va {avg * 100:+.1f}% en promedio)."})
    new = [x for x in new if (x["symbol"], x["kind"]) not in done]
    if not new:
        print("Sin alertas nuevas."); return []
    send(f"<b>QX alerta · {now_et:%H:%M} NY</b> (precios con 16 min de retraso)\n" + "\n".join(x["text"] for x in new))
    store.upsert("alerts", [{"day": t, "symbol": x["symbol"], "kind": x["kind"], "detail": x["text"]} for x in new])
    return new


def main():
    from qx.store import LocalStore, SupabaseStore
    local = "--local" in sys.argv
    store = LocalStore("qx.sqlite") if local else SupabaseStore()
    try:
        run(Alpaca(), store)
    except Exception as e:
        traceback.print_exc()
        try:
            store.log_run("intraday", "error", {"error": str(e)})
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
