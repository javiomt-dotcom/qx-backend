"""Prueba de punta a punta con un Alpaca falso, SQLite local y sin Telegram."""
import datetime as dt
import math
import random
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import daily
from qx import engine as E
from qx.store import LocalStore
from qx.sp500 import SP500

NY = ZoneInfo("America/New_York")


def sessions(end: dt.date, n: int):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


class FakeAlpaca:
    """Mercado sintético: SPY, ~60 del S&P y ~40 de fuera, incluidas centavos y un salto de +60%."""
    def __init__(self, last: dt.date, seed=7):
        R = random.Random(seed)
        self.days = sessions(last, 300)
        self.syms = ["SPY", "QQQ", "IWM", "DIA"] + sorted(SP500)[:60] + [f"X{k:02d}" for k in range(40)]
        self.data = {}
        mkt = [R.gauss(0.0004, 0.009) for _ in self.days]
        for s in self.syms:
            p = 2 + R.random() * 8 if s.startswith("X0") else 20 + R.random() * 300
            vol, base = 0.01 + R.random() * 0.03, (1 + R.random() * 20) * 1e6
            r = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
            for i, d in enumerate(self.days):
                ret = mkt[i] + (0 if s == "SPY" else R.gauss(0, vol))
                if s == "X15" and i == len(self.days) - 1:
                    ret = 0.6
                o = p * (1 + R.gauss(0, vol / 3)); c = p * (1 + ret)
                r["t"].append(d); r["o"].append(o); r["c"].append(c)
                r["h"].append(max(o, c) * (1 + abs(R.gauss(0, vol / 2)))); r["l"].append(min(o, c) * (1 - abs(R.gauss(0, vol / 2))))
                r["v"].append(base * (1 + abs(ret) * 20)); p = c
            self.data[s] = r
        self.calls = 0

    def calendar(self, start, end):
        out, d = [], start
        while d <= end:
            if d.weekday() < 5:
                out.append({"date": d.isoformat(), "close": "16:00"})
            d += dt.timedelta(days=1)
        return out

    def assets(self):
        return [{"symbol": s, "name": s + " Inc", "exchange": "NASDAQ"} for s in self.syms if s not in ("SPY", "QQQ", "IWM", "DIA")]

    def daily_bars(self, symbols, start, chunk=50):
        self.calls += 1
        out = {}
        for s in symbols:
            r = self.data.get(s)
            if not r:
                continue
            k0 = next((k for k, t in enumerate(r["t"]) if t >= start.isoformat()), len(r["t"]))
            out[s] = {k: v[k0:] for k, v in r.items()}
        return out


def test_end_to_end():
    last = dt.date(2026, 10, 21)
    fa = FakeAlpaca(last)
    st = LocalStore()
    sent = []
    now = dt.datetime(2026, 10, 21, 17, 0, tzinfo=NY)
    out = daily.run(fa, st, now_et=now, send=sent.append)
    f, picks = out["forecast"], out["picks"]
    assert f["signal_date"] == "2026-10-21" and f["for_session"] == "2026-10-22"
    assert len(picks) == 10
    assert sum(1 for p in picks if not p["sp500"]) <= 3, "máximo 3 fuera del S&P"
    assert all(p["symbol"] != "X15" for p in picks), "descarta el salto de +60%"
    assert all(p["max_entry"] > p["close"] > p["stop"] and p["target"] > p["close"] for p in picks)
    assert sent and "QX" in sent[0]
    sw = out["swing"]
    assert sw["error"] is None, sw["error"]
    assert len(sw["picks"]) == 10 and sum(1 for p in sw["picks"] if not p["sp500"]) <= 3
    assert all(p["sell_session"] == "2026-10-28" for p in sw["picks"])
    assert "Swing 5 días" in sent[0]
    # mismo día otra vez: no duplica
    assert daily.run(fa, st, now_et=now, send=sent.append) is None

    # día siguiente: califica la lista anterior y genera otra
    fa2 = FakeAlpaca(dt.date(2026, 10, 22))
    for s in fa.data:                 # el pasado no cambia; sólo se agrega el 22
        for k in fa.data[s]:
            fa2.data[s][k] = fa.data[s][k] + fa2.data[s][k][-1:]
    out2 = daily.run(fa2, st, now_et=dt.datetime(2026, 10, 22, 17, 0, tzinfo=NY), send=sent.append)
    y = out2["yesterday"]
    assert y and y["session"] == "2026-10-22"
    assert y["model_ret"] is not None and math.isfinite(y["model_ret"])
    oc = st.select("outcomes", {"signal_date": "eq.2026-10-21"})
    assert len(oc) == 10 and all(o["flag"] in ("OK", "STOP", "TARGET", "SKIPPED") for o in oc)
    assert out2["cum"]["n"] == 1
    assert "Ayer" in sent[-1]
    y5 = out2["swing"]["yesterday"]
    assert out2["swing"]["error"] is None and y5 and y5["session"] == "2026-10-22" and y5["cohorts"] == 1
    assert len(st.select("swing_picks")) == 20


def test_trade_rules():
    # gap mayor a 1 ATR: no se compra
    assert E.trade_at(105, 113, 101, 112, 100, 10)[1] == "OK"
    assert E.trade_at(111, 113, 109, 112, 100, 10)[1] == "SKIPPED"
    # stop gana si el día toca los dos
    r, f = E.trade_at(100, 140, 80, 120, 100, 10)
    assert f == "STOP" and abs(r - (-0.15)) < 1e-9
    r, f = E.trade_at(100, 131, 90, 120, 100, 10)
    assert f == "TARGET" and abs(r - 0.30) < 1e-9


def test_session_cutoff():
    from qx.data_alpaca import last_completed_session
    cal = [{"date": "2026-10-20", "close": "16:00"}, {"date": "2026-10-21", "close": "16:00"}]
    assert last_completed_session(cal, dt.datetime(2026, 10, 21, 16, 10, tzinfo=NY)) == "2026-10-20"
    assert last_completed_session(cal, dt.datetime(2026, 10, 21, 16, 25, tzinfo=NY)) == "2026-10-21"


if __name__ == "__main__":
    test_trade_rules(); test_session_cutoff(); test_end_to_end(); print("ok")
