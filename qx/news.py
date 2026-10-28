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


# --- resumen diario: qué pasó en las noticias, cómo se movió el mercado y qué temas vienen creciendo ---
THEMES = {
    "Fed y tasas": r"\bfed\b|fomc|powell|rate cut|rate hike|interest rate|treasury yield|bond yield",
    "Inflación": r"inflation|\bcpi\b|\bppi\b|\bpce\b|consumer prices",
    "Empleo": r"jobs report|payrolls|jobless claims|unemployment|labor market",
    "Aranceles y comercio": r"tariff|trade war|trade deal|export control|sanction",
    "China": r"\bchina\b|chinese|beijing",
    "IA y chips": r"\bai\b|artificial intelligence|nvidia|semiconductor|\bchips?\b|data center",
    "Petróleo y energía": r"\boil\b|crude|opec|natural gas|energy prices",
    "Resultados": r"earnings|\beps\b|quarterly results|revenue|guidance",
    "Guía al alza": r"raises (?:fy|full-year|annual|guidance|outlook|forecast)|boosts (?:guidance|outlook)",
    "Guía a la baja": r"(?:cuts|lowers|slashes) (?:fy|full-year|annual|guidance|outlook|forecast)",
    "Recomendaciones de analistas": r"upgrade|downgrade|price target|initiates coverage",
    "Fusiones y compras": r"acquire|acquisition|merger|takeover|buyout|to buy\b",
    "Despidos y recortes": r"layoff|job cuts|restructuring",
    "Bancos y crédito": r"\bbanks?\b|credit|lender|default",
    "FDA y salud": r"\bfda\b|clinical trial|drug approval|phase [123]",
    "Cripto": r"bitcoin|crypto|ethereum",
    "Gobierno y política": r"white house|congress|shutdown|election|trump|senate",
}
_THEME_RE = {k: re.compile(v, re.I) for k, v in THEMES.items()}
ETFS = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "IVV", "TLT", "GLD", "USO", "XLE", "XLF", "XLK"}
MACRO = ("Fed y tasas", "Inflación", "Empleo", "Aranceles y comercio", "Petróleo y energía", "Gobierno y política")


def theme_counts(articles):
    out = {k: 0 for k in THEMES}
    for n in articles:
        txt = n["headline"]
        for k, rx in _THEME_RE.items():
            if rx.search(txt):
                out[k] += 1
    return out


def day_rows(articles, d, panel, universe=None):
    """Fila de news_market y filas de news_daily para la sesión d (titulares ya asignados a d)."""
    import numpy as np
    per = {}
    for n in articles:
        tn = tone(n["headline"] + " " + n.get("summary", ""))
        for s in set(n["symbols"]):
            x = per.setdefault(s, [0, 0.0, []]); x[0] += 1; x[1] += tn; x[2].append((tn, n["headline"]))
    i = panel.dates.index(d) if d in panel.dates else None
    idx = panel.idx

    def ret(s):
        r = idx.get(s)
        if r is None or i is None or i == 0:
            return None
        v = panel.c[r, i] / panel.c[r, i - 1] - 1
        return float(v) if np.isfinite(v) else None

    def dvol(s):
        r = idx.get(s)
        if r is None or i is None:
            return 0.0
        v = np.nanmean((panel.c[r, max(0, i - 19):i + 1] * panel.v[r, max(0, i - 19):i + 1]))
        return float(v) if np.isfinite(v) else 0.0

    uni = set(universe) if universe else set(panel.syms)
    movers = []
    for s, (k, t, hs) in per.items():
        if s not in uni or s in ETFS:
            continue
        r = ret(s)
        if r is None:
            continue
        dv = dvol(s)
        if dv <= 0:
            continue
        # el titular que mejor explica el movimiento: el de tono más alineado con el signo del día
        best = max(hs, key=lambda x: x[0] * (1 if r >= 0 else -1))[1]
        movers.append({"s": s, "ret": round(r, 4), "n": k, "tone": round(t / k, 3), "h": best[:180],
                       "score": abs(r) * np.log10(dv)})
    movers.sort(key=lambda x: -x["score"])
    for m in movers:
        m.pop("score")
    macro, seen, per_theme = [], set(), {}
    for n in reversed(articles):                       # los más recientes primero, máximo 2 por tema
        h = n["headline"]
        th = next((k for k in MACRO if _THEME_RE[k].search(h)), None)
        if h in seen or th is None or per_theme.get(th, 0) >= 2 or not set(n["symbols"]) <= ETFS:   # macro: sin acciones
            continue
        seen.add(h); per_theme[th] = per_theme.get(th, 0) + 1
        macro.append(h[:180])
        if len(macro) == 4:
            break
    # ¿el mercado premió las buenas noticias y castigó las malas?
    pos = [m["ret"] for m in movers if m["tone"] > 0.2]
    neg = [m["ret"] for m in movers if m["tone"] < -0.2]
    tones = [tone(n["headline"] + " " + n.get("summary", "")) for n in articles]
    spy = ret("SPY")
    mk = {"d": d, "n": len(articles), "tone": round(float(np.mean(tones)), 4) if tones else None,
          "spy_ret": spy,
          "headlines": {"movers": movers[:8], "macro": macro,
                        "react": {"pos": round(float(np.median(pos)), 4) if len(pos) >= 5 else None, "n_pos": len(pos),
                                  "neg": round(float(np.median(neg)), 4) if len(neg) >= 5 else None, "n_neg": len(neg)}},
          "themes": theme_counts(articles)}
    daily = [{"d": d, "symbol": s, "n": k, "tone": round(t / k, 4)} for s, (k, t, _) in per.items()]
    return mk, daily


def trends(market_rows, daily_rows, last, sessions, min_n5=3):
    """Temas que vienen creciendo (participación de 5 sesiones contra las 20 anteriores) y acciones con atención
    inusual (noticias de 5 sesiones contra su promedio de las 20 anteriores)."""
    k = sessions.index(last)
    recent = set(sessions[max(0, k - 4):k + 1])
    prior = set(sessions[max(0, k - 24):max(0, k - 4)])
    th_r, th_p, n_r, n_p = {}, {}, 0, 0
    for r in market_rows:
        tgt, d = (th_r, "r") if r["d"] in recent else (th_p, "p") if r["d"] in prior else (None, None)
        if tgt is None:
            continue
        if d == "r":
            n_r += r["n"] or 0
        else:
            n_p += r["n"] or 0
        for t, c in (r.get("themes") or {}).items():
            tgt[t] = tgt.get(t, 0) + c
    themes = []
    if n_r and n_p:
        for t in THEMES:
            sr, sp = th_r.get(t, 0) / n_r, th_p.get(t, 0) / n_p
            if th_r.get(t, 0) >= 5:
                themes.append({"theme": t, "share": round(sr, 4), "ratio": round((sr + 1e-4) / (sp + 1e-4), 2)})
        themes.sort(key=lambda x: -x["ratio"])
    nr, npr, tr = {}, {}, {}
    for r in daily_rows:
        if r["d"] in recent:
            nr[r["symbol"]] = nr.get(r["symbol"], 0) + r["n"]
            tr[r["symbol"]] = tr.get(r["symbol"], 0.0) + (r["tone"] or 0) * r["n"]
        elif r["d"] in prior:
            npr[r["symbol"]] = npr.get(r["symbol"], 0) + r["n"]
    n_prior_days = max(1, len(prior))
    att = []
    for s, n5 in nr.items():
        if n5 < min_n5 or s in ETFS:
            continue
        base = npr.get(s, 0) / n_prior_days * len(recent)
        att.append({"s": s, "n5": n5, "x": round((n5 + 1) / (base + 1), 1), "tone": round(tr[s] / n5, 2)})
    att.sort(key=lambda x: -x["x"])
    return {"themes": themes[:6], "attention": att[:8], "full": bool(n_p) and len(prior) >= 15}


def daily(a, panel, last, sessions, store, universe=None, lookback=25, fetch=alpaca_news):
    """Baja los titulares que falten (hasta `lookback` sesiones), los guarda y arma el resumen del día."""
    k = sessions.index(last)
    have = {r["d"] for r in store.select("news_market", {"d": f"gte.{sessions[max(0, k - lookback)]}"})}
    want = [d for d in sessions[max(1, k - lookback + 1):k + 1] if d not in have or d == last]
    if want:
        first = sessions.index(want[0])
        start = dt.datetime.fromisoformat(sessions[first - 1] + "T20:00:00+00:00")
        end = dt.datetime.now(dt.timezone.utc)
        arts = fetch(a, start, end)
        by_day = {}
        for n in arts:
            d = session_of(n["t"], sessions)
            if d in want:
                by_day.setdefault(d, []).append(n)
        for d in want:
            mk, dl = day_rows(by_day.get(d, []), d, panel, universe)
            store.upsert("news_market", [mk])
            store.delete("news_daily", {"d": f"eq.{d}"})
            store.upsert("news_daily", dl)
    lo = sessions[max(0, k - 24)]
    mrows = store.select("news_market", {"d": f"gte.{lo}"}, order="d.asc")
    drows = store.select("news_daily", {"d": f"gte.{lo}"})
    today = next((r for r in mrows if r["d"] == last), None)
    return {"day": today, "trends": trends(mrows, drows, last, sessions), "fetched": len(want)}
