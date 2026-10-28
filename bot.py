"""Comandos del bot de Telegram. Corre en GitHub Actions en ventanas de ~3 horas (de 7am a 10pm hora de México)
y dentro de cada ventana escucha con long polling, así que contesta en segundos.

/lista       lista swing más reciente
/posiciones  listas abiertas, cuánto van y cuándo se venden
/resultados  acumulado real del swing y del modelo de 1 día contra SPY
/noticias    noticias del último cierre y temas en aumento
/ayuda       esta ayuda

Sólo contesta al chat de TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback

import numpy as np
import requests

from qx import config as C, notify, swing as SW
from qx.notify import pct

HELP = ("<b>Comandos de QX</b>\n/lista · lista swing para la próxima sesión\n/posiciones · listas abiertas y cuándo se venden\n"
        "/resultados · acumulado real contra SPY\n/noticias · noticias del último cierre y temas en aumento\n/ayuda · esta ayuda\n\nContesto de 7am a 10pm hora de México; fuera de ese horario, al empezar el día.")


def updates(token, offset=None, session=requests, wait=0):
    r = session.get(f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"timeout": wait, **({"offset": offset} if offset else {})}, timeout=wait + 30)
    r.raise_for_status()
    return r.json().get("result", [])


def cmd_lista(store, a=None, today=None):
    last = store.select("swing_picks", order="signal_date.desc", limit=1)
    if not last:
        return "Todavía no hay listas swing guardadas."
    sig = str(last[0]["signal_date"])
    rows = sorted(store.select("swing_picks", {"signal_date": f"eq.{sig}"}), key=lambda r: r["rank"])
    t = ["#  Símbolo  NoArriba  Acc"] + [f"{r['rank']:<2} {r['symbol'] + ('' if r['sp500'] else '*'):<8} {r['max_entry']:8.2f} {r['shares']:>4}"
                                       for r in rows]
    return (f"<b>Lista swing · señal {sig}</b> (se vende al cierre del {rows[0].get('sell_session') or '—'})\n"
            f"${C.CAPITAL / SW.HOLD / SW.PICKS:,.0f} en cada una, compra en la apertura.\n<pre>" + "\n".join(t) + "</pre>")


def cmd_posiciones(store, a, today=None):
    today = today or dt.date.today()
    t = today.isoformat()
    cal = [r["date"] for r in a.calendar(today - dt.timedelta(days=14), today + dt.timedelta(days=10))]
    past = [d for d in cal if d < t]
    rows = store.select("swing_picks", {"signal_date": f"gte.{past[-SW.HOLD - 1]}"})
    by = {}
    for r in rows:
        by.setdefault(str(r["signal_date"]), []).append(r)
    nxt = lambda d: cal[cal.index(d) + 1] if d in cal and cal.index(d) + 1 < len(cal) else "9999"
    # abiertas: ya se compraron (la sesión siguiente a la señal ya empezó) y no han llegado a su cierre de venta
    live = sorted(s for s, rs in by.items() if nxt(s) <= t and str(rs[0].get("sell_session") or "9999") >= t)
    if not live:
        return "No hay listas swing abiertas ahora."
    bars = a.daily_bars(sorted({r["symbol"] for s in live for r in by[s]}), dt.date.fromisoformat(nxt(live[0])))
    L = ["<b>Posiciones swing abiertas</b> (precios con 16 min de retraso)"]
    for s in live:
        buy, xs = nxt(s), []
        for r in by[s]:
            B = bars.get(r["symbol"])
            if B and buy in B["t"]:
                xs.append(B["c"][-1] / B["o"][B["t"].index(buy)] - 1)
        L.append(f"• Señal {s}: {pct(float(np.mean(xs))) if xs else '—'} · se vende al cierre del {by[s][0].get('sell_session')}\n"
                 f"  {' '.join(r['symbol'] for r in sorted(by[s], key=lambda r: r['rank']))}")
    return "\n".join(L)


def cmd_resultados(store, a=None, today=None):
    sw = [r for r in store.select("swing_results", order="session.asc") if r.get("ret") is not None and r.get("spy_cc") is not None]
    d1 = [r for r in store.select("daily_results", order="signal_date.asc") if r.get("model_ret") is not None and r.get("spy_oc") is not None]
    L = ["<b>Resultados reales</b>"]
    if sw:
        L.append(f"Swing 5 días ({len(sw)} sesiones): {pct(float(np.prod([1 + r['ret'] for r in sw]) - 1), 1)} "
                 f"vs SPY {pct(float(np.prod([1 + r['spy_cc'] for r in sw]) - 1), 1)}")
    else:
        L.append("Swing 5 días: todavía sin sesiones calificadas.")
    if d1:
        L.append(f"Modelo 1 día ({len(d1)} sesiones): {pct(float(np.prod([1 + r['model_ret'] for r in d1]) - 1), 1)} "
                 f"vs SPY {pct(float(np.prod([1 + r['spy_oc'] for r in d1]) - 1), 1)}")
    return "\n".join(L)


def cmd_noticias(store, a=None, today=None):
    from qx import news as NW
    rows = store.select("news_market", order="d.desc", limit=30)
    if not rows:
        return "Todavía no hay noticias guardadas; llegan con la rutina de la tarde."
    rows.reverse()
    last, sessions = rows[-1]["d"], [r["d"] for r in rows]
    drows = store.select("news_daily", {"d": f"gte.{sessions[max(0, len(sessions) - 25)]}"})
    picks = [p["symbol"] for p in store.select("swing_picks", {"signal_date": f"eq.{last}"})]
    return notify.news_message({"day": rows[-1], "trends": NW.trends(rows, drows, last, sessions)}, picks)


COMMANDS = {"/lista": cmd_lista, "/posiciones": cmd_posiciones, "/resultados": cmd_resultados, "/noticias": cmd_noticias}


def run(store, a, token=None, chat=None, send=notify.send, get=updates, today=None):
    token, chat = token or C.TELEGRAM_TOKEN, str(chat or C.TELEGRAM_CHAT)
    ups = get(token)
    answered = 0
    for u in ups:
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) != chat:
            continue                                   # sólo el dueño
        cmd = (m.get("text") or "").strip().split()[0:1]
        cmd = cmd[0].split("@")[0].lower() if cmd else ""
        f = COMMANDS.get(cmd)
        try:
            send(f(store, a, today) if f else HELP)
        except Exception as e:
            traceback.print_exc()
            send(f"⚠️ No pude contestar {cmd}: {type(e).__name__}")
        answered += 1
    if ups:
        get(token, ups[-1]["update_id"] + 1)            # marca como leídos
    if ups:
        print(f"{len(ups)} mensajes, {answered} contestados")
    return answered


def main():
    """Una pasada, o con --loop MIN escucha durante MIN minutos (long polling de 50 segundos)."""
    import argparse
    import time
    from qx.data_alpaca import Alpaca
    from qx.store import SupabaseStore
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0)
    a = ap.parse_args()
    store, alp, end, fails = SupabaseStore(), Alpaca(), time.time() + a.loop * 60, 0
    get = lambda token, offset=None: updates(token, offset, wait=0 if offset or not a.loop else 50)
    while True:
        try:
            run(store, alp, get=get)
            fails = 0
        except Exception:
            traceback.print_exc()
            fails += 1
            if not a.loop or fails >= 5:
                sys.exit(1)
            time.sleep(30)
        if time.time() > end:
            break


if __name__ == "__main__":
    main()
