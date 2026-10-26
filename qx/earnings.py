"""Calendario de reportes de resultados (Alpha Vantage, una sola llamada al día con todo el mercado).

El swing no compra acciones que reportan mientras están en cartera: el salto del reporte es una
apuesta de 50/50 que el modelo no sabe predecir."""
from __future__ import annotations

import csv
import io

import requests

from . import config as C

URL = "https://www.alphavantage.co/query"


def fetch(key: str | None = None, session=None) -> list[dict]:
    """Filas {symbol, report_date, time} de los próximos 3 meses."""
    key = key or C.ALPHAVANTAGE_KEY
    if not key:
        return []
    r = (session or requests).get(URL, params={"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": key}, timeout=60)
    r.raise_for_status()
    text = r.text.strip()
    if not text.lower().startswith("symbol"):     # Alpha Vantage responde 200 con un JSON de aviso cuando falla la llave o el límite
        raise RuntimeError(f"Alpha Vantage no devolvió el calendario: {text[:160]}")
    out, seen = [], set()
    for row in csv.DictReader(io.StringIO(text)):
        s, d = (row.get("symbol") or "").strip().upper(), (row.get("reportDate") or "").strip()
        if s and d and (s, d) not in seen:
            seen.add((s, d))
            out.append({"symbol": s.replace("-", "."), "report_date": d, "time": (row.get("timeOfTheDay") or "").strip() or None})
    return out


def in_window(rows: list[dict], start: str, end: str) -> dict[str, str]:
    """{símbolo: fecha} de los que reportan entre start y end (incluidos)."""
    out = {}
    for r in rows:
        d = str(r["report_date"])[:10]
        if start <= d <= end:
            out.setdefault(r["symbol"], d)
    return out
