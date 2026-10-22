"""Investigación: compara el modelo actual contra versiones nuevas con 3 años de datos de Alpaca.

Walk-forward honesto: cada 20 sesiones se re-entrena sólo con el pasado y se eligen 10 acciones
por día con las reglas realistas de entrada (compra en la apertura, salta gaps > 1 ATR, stop 1.5 ATR,
objetivo 3 ATR, costo de 0.10% ida y vuelta por operación).

Uso:  python research.py [--years 3]
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from zoneinfo import ZoneInfo

import numpy as np

from qx import config as C, engine as E
from qx.data_alpaca import Alpaca, last_completed_session
from qx.sp500 import SP500, INDEXES
from daily import pick_universe, cut

NY = ZoneInfo("America/New_York")
COST = 0.001            # ida y vuelta
WARMUP = 250            # sesiones de entrenamiento antes de la primera prueba
RETRAIN = 20
MAX_ATRP = 0.08


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load(years):
    a = Alpaca()
    now = dt.datetime.now(NY)
    today = now.date()
    cal = a.calendar(today - dt.timedelta(days=40), today + dt.timedelta(days=10))
    last = last_completed_session(cal, now)
    assets = a.assets()
    cands = sorted({x["symbol"] for x in assets} | set(SP500) | set(INDEXES))
    scan = cut(a.daily_bars(cands, today - dt.timedelta(days=35)), last)
    universe = pick_universe(scan)
    log(f"universo actual: {len(universe)} acciones; bajando {years} años…")
    bars = cut(a.daily_bars(universe + list(INDEXES), today - dt.timedelta(days=int(365 * years) + 90)), last)
    return E.Panel.from_bars(bars), universe


def trades(p, i, rows, a):
    """Versión vectorizada de engine.trade_at para el día i -> i+1. Saltadas cuentan 0 (efectivo)."""
    o1, h1, l1, c1, c0 = p.o[rows, i + 1], p.h[rows, i + 1], p.l[rows, i + 1], p.c[rows, i + 1], p.c[rows, i]
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(o1) & np.isfinite(h1) & np.isfinite(l1) & np.isfinite(c1) & np.isfinite(c0) & np.isfinite(a) & (o1 > 0)
        skip = ok & (o1 - c0 > C.GAP_ATR * a)
        stop, tgt = o1 - C.STOP_ATR * a, o1 + C.TGT_ATR * a
        hit_s = l1 <= stop
        ret = np.where(hit_s, stop / o1 - 1, np.where(h1 >= tgt, tgt / o1 - 1, c1 / o1 - 1)) - COST
    ret = np.where(skip, 0.0, ret)
    ret[~ok] = np.nan
    return ret, skip, ok & ~skip & hit_s


def build(p, universe):
    idx = p.idx
    spy = idx["SPY"]
    rows_all = np.array(sorted(idx[s] for s in universe if s in idx))
    is_sp = np.array([p.syms[r] in SP500 for r in rows_all])
    S = {k: [] for k in ("X", "day", "row", "sp", "top", "ret", "skip", "stop", "atrp", "r1", "oc", "cc")}
    days, spy_oc, regime = [], [], []
    for i in range(55, len(p.dates) - 1):
        F = E.raw_features(p, i, spy)[rows_all]
        with np.errstate(invalid="ignore", divide="ignore"):
            dv = np.nanmean((p.c[rows_all, i - 19:i + 1] * p.v[rows_all, i - 19:i + 1]), axis=1)
            liquid = is_sp | ((p.c[rows_all, i] >= C.MIN_PRICE) & (dv >= C.MIN_DOLLAR_VOL))   # filtro con datos de ese día
        ok = np.isfinite(F).all(axis=1) & liquid
        if ok.sum() < min(200, int(len(rows_all) * 0.5)):
            continue
        rows = rows_all[ok]
        Fv = F[ok]
        a = E.atr(p, i)[rows]
        with np.errstate(invalid="ignore", divide="ignore"):
            cc = p.c[rows, i + 1] / p.c[rows, i] - 1
            oc = p.c[rows, i + 1] / p.o[rows, i + 1] - 1
        fin = np.isfinite(cc)
        top = np.zeros(len(rows), bool)
        top[np.argsort(-np.where(fin, cc, -np.inf))[:20]] = True
        top &= fin
        ret, skip, stop = trades(p, i, rows, a)
        d = len(days)
        days.append(i)
        breadth = float((Fv[:, 10] > 0).mean())
        regime.append(E.regime_at(p, spy, i, breadth)["key"])
        spy_oc.append(p.c[spy, i + 1] / p.o[spy, i + 1] - 1)
        S["X"].append(E.zday(Fv).astype(np.float32)); S["day"].append(np.full(len(rows), d, np.int32))
        S["row"].append(rows); S["sp"].append(is_sp[ok]); S["top"].append(top); S["ret"].append(ret)
        S["skip"].append(skip); S["stop"].append(stop); S["atrp"].append(a / p.c[rows, i]); S["r1"].append(Fv[:, 0])
        S["oc"].append(oc); S["cc"].append(cc)
    S = {k: np.concatenate(v) for k, v in S.items()}
    log(f"{len(days)} sesiones con datos, {len(S['day']):,} muestras")
    return S, days, np.array(spy_oc), regime


def weights(day, t):
    return (0.5 ** ((t - 1 - day) / C.HALF_LIFE)).astype(np.float32)


def fit_logit(X, y, W, w0=None, iters=200):
    k = X.shape[1]
    w = np.zeros(k + 1) if w0 is None else w0.copy()
    if w0 is None:
        pr = max(1e-3, float(np.average(y, weights=W)))
        w[k] = np.log(pr / (1 - pr))
    sw = W.sum()
    for _ in range(iters):
        s = X @ w[:k] + w[k]
        e = (1 / (1 + np.exp(-s)) - y) * W
        w[:k] -= 0.8 * ((X.T @ e) / sw + 2e-3 * w[:k])
        w[k] -= 0.8 * e.sum() / sw
    return w


def fit_ridge(X, y, W, lam=50.0):
    Xb = np.hstack([X, np.ones((len(X), 1), np.float32)])
    A = (Xb * W[:, None]).T @ Xb
    A[:-1, :-1] += lam * np.eye(X.shape[1]) * (W.sum() / len(W))
    return np.linalg.solve(A, (Xb * W[:, None]).T @ y)


def select(score, idxs, S, filt, sp_only=False, n=10):
    order = idxs[np.argsort(-score)]
    out, non_sp = [], 0
    for j in order:
        if S["r1"][j] > C.MAX_JUMP:
            continue
        if filt and not (S["atrp"][j] <= MAX_ATRP):
            continue
        sp = S["sp"][j]
        if sp_only and not sp:
            continue
        if not sp and non_sp >= C.MAX_NON_SP:
            continue
        out.append(j); non_sp += (not sp)
        if len(out) == n:
            break
    return out


VARIANTS = [
    ("A. Actual (producción)", "logit", False, False),
    ("B. Actual + filtro de volatilidad", "logit", True, False),
    ("C. Retorno ajustado por riesgo", "ridge_n", True, False),
    ("D. Retorno ÷ volatilidad", "ridge_r", True, False),
    ("E. C sólo S&P 500", "ridge_n", True, True),
]


def walk_forward(S, ndays):
    by_day = [np.where(S["day"] == d)[0] for d in range(ndays)]
    y_top = S["top"].astype(np.float32)
    lab = np.isfinite(S["ret"])
    y_n = np.clip(np.nan_to_num(S["ret"]) / np.maximum(S["atrp"], 1e-3), -3, 3).astype(np.float32)
    y_r = np.clip(np.nan_to_num(S["ret"]), -0.3, 0.3).astype(np.float32)
    res = {v[0]: [] for v in VARIANTS}
    w_l = b_n = b_r = None
    for t in range(WARMUP, ndays):
        if (t - WARMUP) % RETRAIN == 0:
            m = (S["day"] < t) & lab
            X, W = S["X"][m], weights(S["day"][m], t)
            w_l = fit_logit(X, y_top[m], W, w_l, 220 if w_l is None else 60)
            b_n = fit_ridge(X, y_n[m], W)
            b_r = fit_ridge(X, y_r[m], W)
            log(f"re-entrenado en la sesión {t}/{ndays} ({m.sum():,} muestras)")
        ii = by_day[t]
        X = S["X"][ii]
        sc = {"logit": X @ w_l[:-1] + w_l[-1], "ridge_n": X @ b_n[:-1] + b_n[-1],
              "ridge_r": (X @ b_r[:-1] + b_r[-1]) / np.maximum(S["atrp"][ii], 1e-3)}
        for name, model, filt, sp_only in VARIANTS:
            pk = select(sc[model], ii, S, filt, sp_only)
            r = S["ret"][pk]
            r = r[np.isfinite(r)]
            res[name].append({"t": t, "ret": float(r.mean()) if len(r) else 0.0, "hits": int(S["top"][pk].sum()),
                              "stops": int(S["stop"][pk].sum()), "skips": int(S["skip"][pk].sum()),
                              "atrp": float(np.nanmean(S["atrp"][pk])) if pk else np.nan})
    return res, (w_l, b_n, b_r)


def stats(rs):
    r = np.array(rs, float)
    eq = np.cumprod(1 + r)
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sh = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return {"cum": float(eq[-1] - 1), "ann": float(eq[-1] ** (252 / len(r)) - 1), "sharpe": sh, "dd": dd,
            "win": float((r > 0).mean()), "mean": float(r.mean())}


def report(S, days, dates, spy_oc, regime, res):
    T = sorted({x["t"] for v in res.values() for x in v})
    uni = []
    for t in T:
        ii = np.where(S["day"] == t)[0]
        uni.append(float(np.nanmean(S["oc"][ii])))
    spy = [float(spy_oc[t]) for t in T]
    d0, d1 = dates[days[T[0]]], dates[days[T[-1]]]
    print(f"\n## Resultados walk-forward: {len(T)} sesiones ({d0} a {d1}), 10 acciones por día, costo 0.10%\n")
    print("| Variante | Acumulado | Anual | Sharpe | Peor caída | Días +  | Le gana al universo | En top 20 | Stops/día | ATR% medio |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for name, v in res.items():
        r = [x["ret"] for x in v]
        s = stats(r)
        beat = np.mean([a > b for a, b in zip(r, uni)])
        rows.append((name, s))
        print(f"| {name} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | {s['win']:.0%} | {beat:.0%} | "
              f"{np.mean([x['hits'] for x in v]):.2f} | {np.mean([x['stops'] for x in v]):.1f} | {np.nanmean([x['atrp'] for x in v]):.1%} |")
    for name, r in (("Universo (igual peso, apertura a cierre)", uni), ("SPY (apertura a cierre)", spy)):
        s = stats(r)
        print(f"| {name} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | {s['win']:.0%} | — | — | — | — |")
    years = sorted({dates[days[t]][:4] for t in T})
    print("\n### Por año (acumulado)\n")
    print("| Variante | " + " | ".join(years) + " |")
    print("|---|" + "---|" * len(years))
    series = {**{n: [x["ret"] for x in v] for n, v in res.items()}, "Universo": uni, "SPY": spy}
    for name, r in series.items():
        cells = []
        for y in years:
            rr = [x for x, t in zip(r, T) if dates[days[t]][:4] == y]
            cells.append(f"{np.prod(1 + np.array(rr)) - 1:+.1%}" if rr else "—")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print("\n### Por régimen (retorno medio diario)\n")
    print("| Variante | Favorable | Neutral | Débil |")
    print("|---|---|---|---|")
    for name, r in series.items():
        cells = []
        for k in ("fav", "neu", "weak"):
            rr = [x for x, t in zip(r, T) if regime[t] == k]
            cells.append(f"{np.mean(rr):+.3%} ({len(rr)})" if rr else "—")
        print(f"| {name} | " + " | ".join(cells) + " |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3)
    a = ap.parse_args()
    p, universe = load(a.years)
    log(f"panel: {len(p.syms)} símbolos × {len(p.dates)} sesiones ({p.dates[0]} a {p.dates[-1]})")
    S, days, spy_oc, regime = build(p, universe)
    res, models = walk_forward(S, len(days))
    report(S, days, p.dates, spy_oc, regime, res)
    w_l, b_n, b_r = models
    print("\n### Pesos del modelo C (retorno ajustado por riesgo), último entrenamiento\n")
    for j in np.argsort(-np.abs(b_n[:-1])):
        print(f"- {E.FEATS[j][1]}: {b_n[j]:+.3f}")
    print("\nNota: el universo es el de hoy (acciones que siguen listadas), así que hay algo de sesgo de supervivencia; "
          "el filtro de precio y liquidez sí se aplica con los datos de cada día.")


if __name__ == "__main__":
    main()
