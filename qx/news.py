"""Noticias: Alpaca (titulares de Benzinga, gratis y con historia) y, si la llave lo permite, Alpha Vantage
(sentimiento ya calificado). Con los titulares se arman rasgos diarios por acción y un resumen del mercado."""
from __future__ import annotations

import datetime as dt
import re
import time

import requests

from . import config as C
from .data_alpaca import DATA

AV_URL = "https://www.alphavantage.co/query"

# léxico financiero corto (inspirado en Loughran-McDonald) para calificar titulares en inglés
POS = set("""beat beats raise raises raised record surge surges soar soars jump jumps rally rallies gain gains upgrade upgrades
upgraded outperform buy strong stronger growth grow grows profit profitable exceed exceeds exceeded approval approved approves
wins win award awarded partnership expands expansion boost boosts boosted bullish higher tops top topped accelerate accelerates
breakthrough rebound rebounds recover recovers recovery dividend buyback upside optimistic positive acquire""".split())
NEG = set("""miss misses missed cut cuts lower lowers lowered plunge plunges sink sinks drop drops fall falls fell slump slumps
downgrade downgrades downgraded underperform sell weak weaker loss losses decline declines declined warn warns warning lawsuit
sues sued probe investigation recall halt halts delay delays delayed bankruptcy layoffs layoff fraud fine fined bearish
disappoint disappoints disappointing concern concerns risk risks slash slashes tumble tumbles crash crashes default resign
resigns negative downside pessimistic shortfall""".split())
WORD = re.compile(r"[a-z]+")


def tone(text: str) -> float:
    """-1 a 1: (positivas - negativas) / palabras con tono."""
    w = WORD.findall((text or "").lower())
    p, n = sum(x in POS for x in w), sum(x in NEG for x in w)
    return (p - n) / (p + n) if p + n else 0.0


def alpaca_news(a, start: dt.datetime, end: dt.datetime, symbols=None, max_pages=100000):
    """Titulares entre start y end (UTC). a: cliente Alpaca (usa sus llaves y reintentos)."""
    out, token, pages = [], None, 0
    while pages < max_pages:
        p = {"start": start.isoformat(timespec="seconds").replace("+00:00", "Z"),
             "end": end.isoformat(timespec="seconds").replace("+00:00", "Z"),
             "limit": 50, "sort": "asc", "include_content": "false"}
        if symbols:
            p["symbols"] = ",".join(symbols)
        if token:
            p["page_token"] = token
        js = a._get(f"{DATA}/v1beta1/news", p)
        for n in js.get("news", []):
            out.append({"id": n["id"], "t": n["created_at"], "headline": n.get("headline") or "",
                        "summary": n.get("summary") or "", "symbols": n.get("symbols") or [], "source": n.get("source")})
        token, pages = js.get("next_page_token"), pages + 1
        if not token:
            break
    return out


def av_news(key=None, time_from: str | None = None, limit=1000, session=requests):
    key = key or C.ALPHAVANTAGE_KEY
    p = {"function": "NEWS_SENTIMENT", "limit": limit, "sort": "LATEST", "apikey": key}
    if time_from:
        p["time_from"] = time_from
    js = session.get(AV_URL, params=p, timeout=60).json()
    if "feed" not in js:
        raise RuntimeError(f"Alpha Vantage no dio noticias: {str(js)[:160]}")
    return js["feed"]
