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


def session_of(ts_utc: str, sessions: list[str], cutoff_min: int = 16 * 60 + 90):
    """Sesión a la que pertenece un titular: la del mismo día si salió antes de las 17:30 de Nueva York
    (la rutina corre después), si no la siguiente. sessions: fechas ordenadas."""
    from bisect import bisect_left
    from zoneinfo import ZoneInfo
    t = dt.datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
    d = t.date().isoformat()
    if t.hour * 60 + t.minute > cutoff_min:
        d = (t.date() + dt.timedelta(days=1)).isoformat()
    k = bisect_left(sessions, d)
    return sessions[k] if k < len(sessions) else None


def aggregate(articles, sessions):
    """{(sesión, símbolo): [n, suma de tono]} y {sesión: [n, suma de tono]} para todo el mercado."""
    per, mkt = {}, {}
    for n in articles:
        d = session_of(n["t"], sessions)
        if d is None:
            continue
        tn = tone(n["headline"] + " " + n.get("summary", ""))
        m = mkt.setdefault(d, [0, 0.0]); m[0] += 1; m[1] += tn
        for s in set(n["symbols"]):
            x = per.setdefault((d, s), [0, 0.0]); x[0] += 1; x[1] += tn
    return per, mkt


NEWS_FEATS = [("nn1", "Noticias hoy"), ("nab", "Atención inusual 5d"), ("tone1", "Tono de noticias hoy"), ("tone5", "Tono de noticias 5d")]


def features(counts, tones, i):
    """Rasgos de noticias del día i. counts/tones: arrays (símbolos, sesiones) con n y suma de tono por sesión."""
    import numpy as np
    n1 = counts[:, i]
    n5 = counts[:, max(0, i - 4):i + 1].sum(1)
    base = counts[:, max(0, i - 64):max(1, i - 4)].sum(1) / max(1, min(60, i - 4)) * 5
    t5 = tones[:, max(0, i - 4):i + 1].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.stack([np.log1p(n1), np.log1p(n5) - np.log1p(base),
                         np.where(n1 > 0, tones[:, i] / np.maximum(n1, 1), 0.0),
                         np.where(n5 > 0, t5 / np.maximum(n5, 1), 0.0)], axis=1)
