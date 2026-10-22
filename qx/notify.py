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
        r.raise_for_status()
    return True


def pct(x, d=2):
    return "—" if x is None or x != x else f"{x * 100:+.{d}f}%"


REG = {"fav": "Favorable", "neu": "Neutral", "weak": "Débil"}


def daily_message(f: dict, picks: list[dict], y: dict | None, cum: dict | None) -> str:
    L = [f"<b>QX · lista para el {f['for_session']}</b>",
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
