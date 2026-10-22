"""Precios desde Alpaca (cuenta gratis de paper trading).

- Activos y calendario: API de trading (paper).
- Barras diarias: API de datos. Con el plan gratis el feed SIP (todo el mercado) se puede
  pedir siempre que el final de la consulta tenga más de 15 minutos; por eso `end` se fija
  16 minutos atrás. Las barras vienen ajustadas por splits.
"""
from __future__ import annotations

import datetime as dt
import time
import requests

from . import config as C

TRADING = "https://paper-api.alpaca.markets"
LIVE = "https://api.alpaca.markets"
DATA = "https://data.alpaca.markets"
ETF_WORDS = (" ETF", " ETN", "ISHARES", "SPDR", "PROSHARES", "DIREXION", " FUND", "INVESCO QQQ", "VANGUARD")


class Alpaca:
    def __init__(self, key=None, secret=None, session=None):
        key, secret = key or C.ALPACA_KEY, secret or C.ALPACA_SECRET
        if secret[:2] in ("PK", "AK") and key[:2] not in ("PK", "AK"):   # se guardaron al revés
            key, secret = secret, key
        self.trading = LIVE if key.startswith("AK") else TRADING          # llaves de cuenta real
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        print(f"Alpaca: llave {key[:2]}… ({len(key)} caracteres), secreto de {len(secret)} caracteres, {self.trading}")
        self.s = session or requests.Session()

    def _get(self, url, params=None, tries=5):
        for k in range(tries):
            r = self.s.get(url, headers=self.h, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(30, 2 ** k))
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    # --- trading API ---
    def assets(self):
        rows = self._get(f"{self.trading}/v2/assets", {"status": "active", "asset_class": "us_equity"})
        out = []
        for a in rows:
            name = (a.get("name") or "").upper()
            if not a.get("tradable") or a.get("exchange") not in C.EXCHANGES:
                continue
            if any(w in name for w in ETF_WORDS):
                continue
            out.append({"symbol": a["symbol"], "name": a.get("name") or "", "exchange": a["exchange"]})
        return out

    def calendar(self, start: dt.date, end: dt.date):
        rows = self._get(f"{self.trading}/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()})
        return [{"date": r["date"], "close": r["close"]} for r in rows]

    # --- data API ---
    def daily_bars(self, symbols: list[str], start: dt.date, chunk: int = 50):
        """Devuelve {símbolo: {"t": [...], "o": [...], ...}} con barras diarias definitivas."""
        end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat(timespec="seconds")
        out: dict[str, dict] = {}
        for i in range(0, len(symbols), chunk):
            batch = symbols[i:i + chunk]
            token = None
            while True:
                params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": start.isoformat(),
                          "end": end, "limit": 10000, "adjustment": "split", "feed": "sip"}
                if token:
                    params["page_token"] = token
                js = self._get(f"{DATA}/v2/stocks/bars", params)
                for s, bars in (js.get("bars") or {}).items():
                    r = out.setdefault(s, {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []})
                    for b in bars:
                        r["t"].append(b["t"][:10]); r["o"].append(b["o"]); r["h"].append(b["h"])
                        r["l"].append(b["l"]); r["c"].append(b["c"]); r["v"].append(b["v"])
                token = js.get("next_page_token")
                if not token:
                    break
        return out


def last_completed_session(cal: list[dict], now_et: dt.datetime) -> str:
    """Última sesión cerrada hace al menos 20 minutos (hora de Nueva York)."""
    today = now_et.date().isoformat()
    done = None
    for r in cal:
        if r["date"] < today:
            done = r["date"]
        elif r["date"] == today:
            hh, mm = map(int, r["close"].split(":"))
            if now_et.hour * 60 + now_et.minute >= hh * 60 + mm + 20:
                done = r["date"]
    return done
