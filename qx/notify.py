"""Mensajes al celular por Telegram."""
from __future__ import annotations

import requests

from . import config as C


def send(text: str, token=None, chat=None, session=None) -> bool:
    token, chat = token or C.TELEGRAM_TOKEN, chat or C.TELEGRAM_CHAT
    if not token or not chat:
        print("[telegram desactivado]\n" + text)
        return False
    s = session or requests
    for k in range(0, len(text), 3900):            # Telegram corta en 4096 caracteres
        r = s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text[k:k + 3900], "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=30)
        if r.status_code in (401, 404):
            raise RuntimeError("Telegram rechazó el token del bot: revisa el secreto BOT / TELEGRAM_BOT_TOKEN")
        if r.status_code == 400:
            raise RuntimeError(f"Telegram rechazó el chat id ({r.json().get('description', '')}): revisa TELEGRAM_CHAT_ID "
                               "y que le hayas mandado un mensaje a tu bot")
        r.raise_for_status()
    return True


def pct(x, d=2):
    return "—" if x is None or x != x else f"{x * 100:+.{d}f}%"


REG = {"fav": "Favorable", "neu": "Neutral", "weak": "Débil"}


def daily_message(f: dict, picks: list[dict], y: dict | None, cum: dict | None) -> str:
    L = [f"<b>Modelo anterior (1 día, en comparación) · {f['for_session']}</b>",
         f"Señal: cierre del {f['signal_date']} · régimen {REG[f['regime']]} "
         f"(tamaño {int(C.REGIME_MULT[f['regime']] * 100)}%) · universo {f['universe']} acciones"]
    if y or cum:
        L.append("")
    if y:
        L.append(f"<b>Ayer ({y['session']}):</b> modelo {pct(y['model_ret'])} vs SPY {pct(y['spy_oc'])}"
                 f" · {y['hits']} en el top · {y['stops']} stop · {y['skipped']} saltadas por gap")
    if cum and cum.get("n"):
        L.append(f"<b>Acumulado real ({cum['n']} sesiones):</b> modelo {pct(cum['model'], 1)} vs SPY {pct(cum['spy'], 1)}"
                 f" · le ganó {cum['beat']} de {cum['n']} días")
    L.append("")
    rows = ["#  Símbolo  Prob  NoArriba   Stop  Objetivo  Acc"]
    for p in picks:
        tag = "" if p["sp500"] else "*"
        rows.append(f"{p['rank']:<2} {p['symbol'] + tag:<8} {p['p_cal'] * 100:4.1f}% {p['max_entry']:8.2f} "
                    f"{p['stop']:7.2f} {p['target']:8.2f} {p['shares']:>4}")
    L.append("<pre>" + "\n".join(rows) + "</pre>")
    L.append("* fuera del S&P 500. Compra en la apertura; si abre arriba de «NoArriba», sáltala. "
             "Stop y objetivo están calculados desde el cierre. No es asesoría financiera.")
    return "\n".join(L)


def swing_message(sw: dict, for_session: str) -> str:
    """Sección principal: swing de 5 días."""
    if sw.get("error"):
        return f"⚠️ <b>Swing 5 días:</b> no se pudo calcular hoy ({sw['error']})."
    L = [f"<b>QX Swing 5 días · {for_session}</b>",
         f"Compra en la apertura ${C.CAPITAL / 5 / 10:,.0f} de cada una (1/5 del capital entre 10) y mantén 5 sesiones. "
         "Si el precio es mayor que eso, usa acciones fraccionarias."]
    y, cum = sw.get("yesterday"), sw.get("cum")
    if y:
        L.append(f"<b>Ayer ({y['session']}):</b> swing {pct(y['ret'])} vs SPY {pct(y['spy_cc'])} · {y['cohorts']} grupos activos")
    if cum and cum.get("n"):
        L.append(f"<b>Acumulado real ({cum['n']} sesiones):</b> swing {pct(cum['model'], 1)} vs SPY {pct(cum['spy'], 1)}")
    rows = ["#  Símbolo  NoArriba  Acc"]
    for p in sw.get("picks", []):
        tag = "" if p["sp500"] else "*"
        rows.append(f"{p['rank']:<2} {p['symbol'] + tag:<8} {p['max_entry']:8.2f} {p['shares']:>4}")
    L.append("<b>Comprar:</b>")
    L.append("<pre>" + "\n".join(rows) + "</pre>")
    if sw.get("sells"):
        L.append(f"<b>Vender al cierre:</b> {', '.join(sw['sells'])} (compradas con la señal del {sw.get('sell_signal')})")
    if sw.get("earn_skipped"):
        L.append(f"<b>Fuera por reporte de resultados:</b> {', '.join(sw['earn_skipped'])}")
    if sw.get("earn_note"):
        L.append(f"ℹ️ Filtro de reportes: {sw['earn_note']}.")
    L.append("En prueba en paralelo durante 4 semanas.")
    return "\n".join(L)


def news_message(nw: dict, picks=()) -> str:
    """Sección de noticias: qué pasó hoy, cómo reaccionó el mercado y qué temas vienen creciendo."""
    if not nw or nw.get("error"):
        return f"⚠️ <b>Noticias:</b> no se pudieron leer hoy ({(nw or {}).get('error', 'sin datos')})."
    day, tr = nw.get("day") or {}, nw.get("trends") or {}
    h = day.get("headlines") or {}
    L = [f"<b>📰 Noticias del día · {day.get('d', '')}</b>",
         f"SPY {pct(day.get('spy_ret'))} · {day.get('n', 0):,} titulares · tono {day.get('tone') or 0:+.2f} (−1 a +1)"]
    rc = h.get("react") or {}
    if rc.get("pos") is not None and rc.get("neg") is not None:
        L.append(f"Reacción: con noticia buena {pct(rc['pos'])} · con noticia mala {pct(rc['neg'])} (mediana)")
    if h.get("macro"):
        L.append("<b>Macro:</b>")
        L += [f"• {esc(x)}" for x in h["macro"][:3]]
    if h.get("movers"):
        L.append("<b>Lo que movió acciones:</b>")
        L += [f"• {m['s']} {pct(m['ret'], 1)}: {esc(m['h'])}" for m in h["movers"][:5]]
    if tr.get("themes"):
        up = [t for t in tr["themes"] if t["ratio"] >= 1.3][:4]
        if up:
            L.append("<b>Temas en aumento (5 días vs 20):</b> " + ", ".join(f"{t['theme']} ×{t['ratio']:.1f}" for t in up))
    if tr.get("attention"):
        L.append("<b>Atención inusual:</b> " + ", ".join(f"{a['s']} ×{a['x']:.0f}" for a in tr["attention"][:6]))
    att = {a["s"]: a for a in tr.get("attention", [])}
    flag = [f"{s} (tono {att[s]['tone']:+.1f})" for s in picks if s in att]
    if flag:
        L.append("<b>Tus compras con mucha noticia:</b> " + ", ".join(flag))
    if not tr.get("full"):
        L.append("ℹ️ Las tendencias usan menos de 20 sesiones de historia; se completan solas en unos días.")
    return "\n".join(L)


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
