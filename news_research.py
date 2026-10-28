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


def today(save=False):
    """Resumen diario con datos reales sin mandar Telegram. save: lo guarda en Supabase (llena las tablas de noticias)."""
    import time
    from qx import engine as E, notify
    from qx.data_alpaca import last_completed_session
    from qx.sp500 import SP500, INDEXES
    from qx.store import LocalStore
    from zoneinfo import ZoneInfo
    a = Alpaca()
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    cal = a.calendar(now.date() - dt.timedelta(days=40), now.date() + dt.timedelta(days=10))
    last = last_completed_session(cal, now)
    bars = a.daily_bars(sorted(SP500) + list(INDEXES), now.date() - dt.timedelta(days=60))
    for r in bars.values():
        n = sum(1 for t in r["t"] if t <= last)
        for k in r:
            r[k] = r[k][:n]
    panel = E.Panel.from_bars({s: r for s, r in bars.items() if r["t"]})
    t0 = time.time()
    if save:
        from qx.store import SupabaseStore
        st = SupabaseStore()
    else:
        st = LocalStore()
    nw = N.daily(a, panel, last, [r["date"] for r in cal], st, sorted(SP500))
    log(f"{nw['fetched']} sesiones bajadas en {time.time() - t0:.0f} s")
    print(notify.news_message(nw))


def fetch_all(a, start, end, threads=3):
    """Baja todos los titulares entre start y end en trozos de 7 días, en paralelo."""
    from concurrent.futures import ThreadPoolExecutor
    chunks, d = [], start
    while d < end:
        chunks.append((d, min(end, d + dt.timedelta(days=7)))); d += dt.timedelta(days=7)
    out, done = [], 0
    with ThreadPoolExecutor(threads) as ex:
        for part in ex.map(lambda c: N.alpaca_news(a, c[0], c[1]), chunks):
            out += part; done += 1
            if done % 10 == 0:
                log(f"noticias: {done}/{len(chunks)} semanas, {len(out):,} titulares")
    return out


def research(years):
    import numpy as np
    import research as R
    from qx import engine as E
    p, universe = R.load(years)
    R.log(f"panel: {len(p.syms)} × {len(p.dates)} ({p.dates[0]} a {p.dates[-1]})")
    a = Alpaca()
    start = dt.datetime.fromisoformat(p.dates[0] + "T00:00:00+00:00")
    end = dt.datetime.now(dt.timezone.utc)
    arts = fetch_all(a, start, end)
    R.log(f"{len(arts):,} titulares de {start.date()} a {end.date()}")
    per, mkt = N.aggregate(arts, p.dates)
    idx, di = p.idx, {d: k for k, d in enumerate(p.dates)}
    counts = np.zeros((len(p.syms), len(p.dates))); tones = np.zeros_like(counts)
    for (d, s), (n, t) in per.items():
        if s in idx and d in di:
            counts[idx[s], di[d]] = n; tones[idx[s], di[d]] = t
    cov = (counts.sum(1) > 0).mean()
    R.log(f"acciones del panel con alguna noticia: {cov:.0%}")
    save_daily(per, p.dates[-260:])
    # muestras de research.build, más rasgos de noticias alineados por (día, fila)
    S, days, spy_oc, spy_cc, regime = R.build(p, universe)
    NF = np.zeros((len(S["day"]), len(N.NEWS_FEATS)), np.float32)
    for d in range(len(days)):
        ii = np.where(S["day"] == d)[0]
        f = N.features(counts, tones, days[d])[S["row"][ii]]
        NF[ii] = E.zday(f)
    mt = np.array([(mkt.get(p.dates[i], [0, 0])[1] / max(1, mkt.get(p.dates[i], [1, 0])[0])) for i in days])
    report_ic(S, NF, days, p, mt)
    run_variants(R, S, NF, days, p, spy_oc, spy_cc, regime)


def report_ic(S, NF, days, p, mt):
    """Correlación de rango (por día) entre cada rasgo de noticias y el retorno a 5 días ajustado por volatilidad."""
    import numpy as np
    g5 = np.nansum(S["g"], axis=1) / np.maximum(S["atrp"] * np.sqrt(5), 1e-3)
    print("\n## ¿Las noticias anticipan el retorno a 5 días?\n")
    print("Correlación de rango promedio por día (IC) entre el rasgo y el retorno de los 5 días siguientes, ajustado por volatilidad. "
          "0.02 o más ya es útil en la práctica; el t indica si es distinto de cero.\n")
    print("| Rasgo | IC medio | t | Días con IC > 0 |")
    print("|---|---|---|---|")
    feats = [(N.NEWS_FEATS[k][1], NF[:, k]) for k in range(NF.shape[1])] + [("Tendencia (vs media 50d), referencia", S["X"][:, 10])]
    for name, x in feats:
        ics = []
        for d in range(len(days)):
            ii = np.where(S["day"] == d)[0]
            ok = np.isfinite(g5[ii])
            if ok.sum() < 50 or np.std(x[ii][ok]) == 0:
                continue
            ra = np.argsort(np.argsort(x[ii][ok])); rb = np.argsort(np.argsort(g5[ii][ok]))
            ics.append(np.corrcoef(ra, rb)[0, 1])
        ics = np.array(ics)
        print(f"| {name} | {ics.mean():+.4f} | {ics.mean() / (ics.std() / np.sqrt(len(ics))):.1f} | {(ics > 0).mean():.0%} |")
    spy = p.idx["SPY"]
    fwd = np.array([p.c[spy, min(i + 5, len(p.dates) - 1)] / p.c[spy, i] - 1 for i in days])
    print(f"\nTono de todo el mercado vs SPY 5 días después: correlación {np.corrcoef(mt, fwd)[0, 1]:+.3f} "
          f"({len(days)} sesiones). Días de tono en el 20% más negativo: SPY 5d medio {fwd[mt <= np.quantile(mt, .2)].mean():+.2%} "
          f"vs resto {fwd[mt > np.quantile(mt, .2)].mean():+.2%}.")


def run_variants(R, S, NF, days, p, spy_oc, spy_cc, regime):
    import numpy as np
    HOLD = R.HOLD
    lab5 = np.isfinite(S["g"]).all(axis=1)
    y_5 = np.clip(np.nansum(S["g"], axis=1) / np.maximum(S["atrp"] * np.sqrt(HOLD), 1e-3), -3, 3).astype(np.float32)
    XN = np.hstack([S["X"], NF])
    by_day = [np.where(S["day"] == d)[0] for d in range(len(days))]
    V = ["L. Swing actual (modelo + tendencia)", "L + noticias en el modelo", "L sin las de noticias negativas",
         "L + noticias, sin las de noticias negativas"]
    res = {v: [] for v in V}
    b = bn = None
    for t in range(R.WARMUP, len(days)):
        if (t - R.WARMUP) % R.RETRAIN == 0:
            m5 = (S["day"] < t - HOLD) & lab5
            W = R.weights(S["day"][m5], t)
            b = R.fit_ridge(S["X"][m5], y_5[m5], W)
            bn = R.fit_ridge(XN[m5], y_5[m5], W)
            R.log(f"re-entrenado {t}/{len(days)}")
        ii = by_day[t]
        def mix(bb, X):
            f = X @ bb[:-1] + bb[-1]
            return (f - f.mean()) / (f.std() or 1) + S["X"][ii][:, 10]
        s0, s1 = mix(b, S["X"][ii]), mix(bn, XN[ii])
        neg = NF[ii][:, 3] < -1.0          # tono 5d más de 1 desviación abajo del día
        for name, sc in ((V[0], s0), (V[1], s1), (V[2], np.where(neg, -1e9, s0)), (V[3], np.where(neg, -1e9, s1))):
            pk = R.select(sc, ii, S, True)
            path = S["g"][pk].copy(); path[:, 0] -= R.COST
            ret = np.nan_to_num(np.nanmean(path, axis=0)) if len(pk) else np.zeros(HOLD)
            res[name].append({"t": t, "ret": ret, "hold": HOLD, "hits": 0, "stops": 0, "skips": 0,
                              "atrp": float(np.nanmean(S["atrp"][pk])) if pk else np.nan})
    R.report(S, days, p.dates, spy_oc, spy_cc, regime, res)
    print("\n### Pesos de las noticias en el modelo (último entrenamiento, junto a los 12 rasgos de precio)\n")
    for k, (_, nm) in enumerate(N.NEWS_FEATS):
        print(f"- {nm}: {bn[12 + k]:+.3f}")


def save_daily(per, keep_dates):
    """Guarda en Supabase el conteo y tono por acción y sesión (el último año), para la rutina diaria."""
    if not (C.SUPABASE_URL and C.SUPABASE_KEY):
        return
    from qx.store import SupabaseStore
    keep = set(keep_dates)
    rows = [{"d": d, "symbol": s, "n": n, "tone": round(t / n, 4)} for (d, s), (n, t) in per.items() if d in keep]
    SupabaseStore().upsert("news_daily", rows)
    log(f"guardadas {len(rows):,} filas de noticias por acción en Supabase")


def log(msg):
    import time
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe", "research", "today", "save"])
    ap.add_argument("--years", type=float, default=3)
    a = ap.parse_args()
    {"probe": probe, "today": today, "save": lambda: today(True)}.get(a.mode, lambda: research(a.years))()
