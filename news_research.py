"""Investigación de noticias: prueba de fuentes y, después, si los rasgos de noticias mejoran el swing."""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from qx import config as C, news as N
from qx.data_alpaca import Alpaca


def probe():
    a = Alpaca()
    end = dt.datetime.now(dt.timezone.utc)
    try:
        ns = N.alpaca_news(a, end - dt.timedelta(days=1), end, max_pages=3)
        print(f"Alpaca noticias: OK, {len(ns)} titulares en 3 páginas")
        for n in ns[:5]:
            print(f"  {n['t'][:16]} {','.join(n['symbols'][:4]):<16} tono {N.tone(n['headline']):+.2f} | {n['headline'][:90]}")
        old = N.alpaca_news(a, end - dt.timedelta(days=1100), end - dt.timedelta(days=1099), max_pages=2)
        print(f"Alpaca historia de hace 3 años: {len(old)} titulares")
    except Exception as e:
        print(f"Alpaca noticias: FALLA {type(e).__name__}: {e}")
    if C.ALPHAVANTAGE_KEY:
        try:
            f = N.av_news(limit=50)
            print(f"Alpha Vantage noticias: OK, {len(f)} artículos con sentimiento")
        except Exception as e:
            print(f"Alpha Vantage noticias: FALLA {e}")
    else:
        print("Alpha Vantage: sin llave")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe"])
    a = ap.parse_args()
    probe()
