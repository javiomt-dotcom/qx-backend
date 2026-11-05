"""Investigación: ¿cuánto del resultado del swing viene del sesgo de universo y del riesgo de mercado?

Corre el mismo walk-forward del swing de 5 días (versiones F, I, K y L de research.py) con dos universos:

1. "Universo de hoy" (como research.py): acciones que hoy siguen listadas y hoy son líquidas, más la lista
   actual del S&P 500. Tiene sesgo de supervivencia y de éxito.
2. "Universo de cada día": todas las acciones de Alpaca, activas y deslistadas. Cada día entran sólo las que
   ese día cumplen precio >= $5 y $5M diarios (promedio de 20 días). En lugar de la lista actual del S&P se
   usan "las 500 más negociadas ese día" (grandes), así que nada depende de lo que pasó después.

Para cada versión reporta, además del acumulado y el Sharpe: beta y alfa contra el SPY, intervalo de 95% del
alfa y del Sharpe con bootstrap por bloques (los retornos de días seguidos no son independientes), y el
resultado con costos de 0.10%, 0.20% y 0.30% por operación (ida y vuelta).

Uso:  python research_bias.py [--years 3]
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from zoneinfo import ZoneInfo

import numpy as np

from qx import config as C, engine as E
from qx.data_alpaca import Alpaca, ETF_WORDS, last_completed_session
from qx.sp500 import SP500, INDEXES
from daily import pick_universe, cut
from research import fit_ridge, weights, stats

NY = ZoneInfo("America/New_York")
WARMUP = 250
RETRAIN = 20
MAX_ATRP = 0.08
HOLD = 5
BIG_N = 500                       # "grandes" del universo de cada día: las 500 con más dólares negociados
COSTS = (0.001, 0.002, 0.003)
BLOCK = 10                        # días por bloque en el bootstrap
BOOT = 2000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- datos

def all_assets(a: Alpaca):
    """Acciones de EE. UU. activas y deslistadas (sin ETFs). Las deslistadas suelen aparecer como OTC."""
    out = {}
    for status in ("active", "inactive"):
        rows = a._get(f"{a.trading}/v2/assets", {"status": status, "asset_class": "us_equity"})
        for x in rows:
            name = (x.get("name") or "").upper()
            if any(w in name for w in ETF_WORDS):
                continue
            if status == "active" and (not x.get("tradable") or x.get("exchange") not in C.EXCHANGES):
                continue
            out[x["symbol"]] = status
    return out


def ever_liquid(r) -> bool:
    """¿Cumplió algún día precio >= MIN_PRICE y promedio de 20 días de dólares >= MIN_DOLLAR_VOL?"""
    c, v = np.asarray(r["c"], float), np.asarray(r["v"], float)
    if len(c) < 20:
        return False
    dv = np.convolve(c * v, np.ones(20) / 20, mode="valid")
    return bool(((c[19:] >= C.MIN_PRICE) & (dv >= C.MIN_DOLLAR_VOL)).any())


def load(years):
    a = Alpaca()
    now = dt.datetime.now(NY)
    today = now.date()
    cal = a.calendar(today - dt.timedelta(days=40), today + dt.timedelta(days=10))
    last = last_completed_session(cal, now)
    start = today - dt.timedelta(days=int(365 * years) + 90)

    # universo de hoy, igual que research.py
    active = [x["symbol"] for x in a.assets()]
    scan = cut(a.daily_bars(sorted(set(active) | set(SP500) | set(INDEXES)), today - dt.timedelta(days=35)), last)
    today_uni = pick_universe(scan)
    log(f"universo de hoy: {len(today_uni)} acciones")

    # todas las acciones, activas y deslistadas; se quedan sólo las que alguna vez fueron líquidas
    st = all_assets(a)
    cands = sorted(set(st) | set(today_uni) | set(INDEXES))
    log(f"candidatas: {len(cands)} ({sum(1 for s in st.values() if s == 'inactive')} deslistadas); bajando {years} años…")
    bars = {}
    for k in range(0, len(cands), 500):
        part = cut(a.daily_bars(cands[k:k + 500], start), last)
        for s, r in part.items():
            if s in INDEXES or s in today_uni or ever_liquid(r):
                bars[s] = r
        log(f"  {min(k + 500, len(cands))}/{len(cands)} revisadas, {len(bars)} guardadas")
    p = E.Panel.from_bars(bars)
    n_inactive = sum(1 for s in p.syms if st.get(s) == "inactive")
    log(f"panel: {len(p.syms)} símbolos ({n_inactive} deslistados) × {len(p.dates)} sesiones ({p.dates[0]} a {p.dates[-1]})")
    return p, today_uni, n_inactive


# ---------------------------------------------------------------- muestras

def build(p, universe, mode):
    """mode "hoy": como research.py (S&P actual siempre entra). mode "dia": filtro y grandes con datos de ese día."""
    idx = p.idx
    spy = idx["SPY"]
    rows_all = np.array(sorted(idx[s] for s in universe if s in idx))
    is_sp = np.array([p.syms[r] in SP500 for r in rows_all])
    S = {k: [] for k in ("X", "day", "big", "atrp", "r1", "cc", "g")}
    days, spy_cc = [], []
    for i in range(55, len(p.dates) - HOLD):
        F = E.raw_features(p, i, spy)[rows_all]
        with np.errstate(invalid="ignore", divide="ignore"):
            dv = np.nanmean(p.c[rows_all, i - 19:i + 1] * p.v[rows_all, i - 19:i + 1], axis=1)
            filt = (p.c[rows_all, i] >= C.MIN_PRICE) & (dv >= C.MIN_DOLLAR_VOL)
        if mode == "hoy":
            liquid, big = is_sp | filt, is_sp
        else:
            liquid = filt
            dvl = np.where(liquid & np.isfinite(dv), dv, -np.inf)
            big = np.zeros(len(rows_all), bool)
            big[np.argsort(-dvl)[:BIG_N]] = True
            big &= liquid
        ok = np.isfinite(F).all(axis=1) & liquid
        if ok.sum() < 200:
            continue
        rows = rows_all[ok]
        a = E.atr(p, i)[rows]
        with np.errstate(invalid="ignore", divide="ignore"):
            oc = p.c[rows, i + 1] / p.o[rows, i + 1] - 1
            g = np.stack([oc] + [p.c[rows, i + k] / p.c[rows, i + k - 1] - 1 for k in range(2, HOLD + 1)], axis=1)
        d = len(days)
        days.append(i)
        spy_cc.append(p.c[spy, i + 1] / p.c[spy, i] - 1)
        S["X"].append(E.zday(F[ok]).astype(np.float32)); S["day"].append(np.full(len(rows), d, np.int32))
        S["big"].append(big[ok]); S["atrp"].append(a / p.c[rows, i]); S["r1"].append(F[ok][:, 0])
        S["cc"].append(p.c[rows, i + 1] / p.c[rows, i] - 1); S["g"].append(g.astype(np.float32))
    S = {k: np.concatenate(v) for k, v in S.items()}
    log(f"[{mode}] {len(days)} sesiones, {len(S['day']):,} muestras, {len(rows_all)} símbolos posibles")
    return S, days, np.array(spy_cc)


def select(score, ii, S, big_only, n=10):
    out, small = [], 0
    for j in ii[np.argsort(-score)]:
        if S["r1"][j] > C.MAX_JUMP or not (S["atrp"][j] <= MAX_ATRP):
            continue
        big = S["big"][j]
        if big_only and not big:
            continue
        if not big and small >= C.MAX_NON_SP:
            continue
        out.append(j); small += (not big)
        if len(out) == n:
            break
    return out


VARIANTS = [   # (nombre, puntaje, sólo grandes)
    ("F. Modelo de retorno a 5 días", "ridge_5", False),
    ("I. Tendencia (media 50d)", "trend", False),
    ("K. Tendencia sólo grandes / S&P", "trend", True),
    ("L. Mezcla F + I (producción)", "mix", False),
]


def walk_forward(S, ndays):
    by_day = [np.where(S["day"] == d)[0] for d in range(ndays)]
    lab5 = np.isfinite(S["g"]).all(axis=1)
    y5 = np.clip(np.nansum(S["g"], axis=1) / np.maximum(S["atrp"] * np.sqrt(HOLD), 1e-3), -3, 3).astype(np.float32)
    res = {v[0]: [] for v in VARIANTS}
    b5 = None
    for t in range(WARMUP, ndays):
        if (t - WARMUP) % RETRAIN == 0:
            m = (S["day"] < t - HOLD) & lab5             # el resultado a 5 días sólo se conoce 5 sesiones después
            b5 = fit_ridge(S["X"][m], y5[m], weights(S["day"][m], t))
        ii = by_day[t]
        X = S["X"][ii]
        f5 = X @ b5[:-1] + b5[-1]
        sc = {"ridge_5": f5, "trend": X[:, 10], "mix": (f5 - f5.mean()) / (f5.std() or 1) + X[:, 10]}
        for name, model, big_only in VARIANTS:
            pk = select(sc[model], ii, S, big_only)
            # retorno de la cohorte por día, sin costo; una acción que deja de tener precio sale del promedio
            path = np.nan_to_num(np.nanmean(S["g"][pk], axis=0)) if pk else np.zeros(HOLD)
            res[name].append({"t": t, "path": path, "big": int(S["big"][pk].sum()) if pk else 0})
    return res


def daily(v, cost):
    """Cada día entra una cohorte con 1/HOLD del capital; el costo ida y vuelta se cobra el día de compra."""
    out = []
    for j in range(len(v)):
        parts = [v[j - k]["path"][k] - (cost if k == 0 else 0.0) for k in range(HOLD) if j - k >= 0]
        out.append(float(np.sum(parts)) / HOLD)
    return np.array(out)


# ---------------------------------------------------------------- estadística

def ols(r, m):
    X = np.column_stack([np.ones(len(m)), m])
    a, b = np.linalg.lstsq(X, r, rcond=None)[0]
    return a, b


def boot(r, m, rng):
    """Bootstrap circular por bloques: intervalo de 95% del alfa anual, del Sharpe y de la ventaja anual sobre SPY."""
    n = len(r)
    nb = int(np.ceil(n / BLOCK))
    al, sh, ex = [], [], []
    for _ in range(BOOT):
        st = rng.integers(0, n, nb)
        ix = ((st[:, None] + np.arange(BLOCK)[None, :]) % n).ravel()[:n]
        rr, mm = r[ix], m[ix]
        a, _ = ols(rr, mm)
        al.append(a * 252)
        sh.append(rr.mean() / rr.std() * np.sqrt(252) if rr.std() > 0 else 0)
        ex.append((rr.mean() - mm.mean()) * 252)
    q = lambda x: (float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5)), float(np.mean(np.array(x) > 0)))
    return q(al), q(sh), q(ex)


def report(title, res, days, dates, spy_cc, rng):
    T = [x["t"] for x in next(iter(res.values()))]
    spy = np.array([spy_cc[t] for t in T])
    d0, d1 = dates[days[T[0]]], dates[days[T[-1]]]
    print(f"\n## {title}: {len(T)} sesiones de prueba ({d0} a {d1})\n")
    print("| Versión | Costo | Acumulado | Anual | Sharpe (IC 95%) | Peor caída | Beta | Alfa anual (IC 95%) | Prob. alfa > 0 | Ventaja anual vs SPY (IC 95%) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    out = {}
    for name, v in res.items():
        for cost in COSTS:
            r = daily(v, cost)
            s = stats(r)
            a, b = ols(r, spy)
            (al, ah, ap), (sl, shh, _), (el, eh, _) = boot(r, spy, rng)
            out[(name, cost)] = {**s, "beta": b, "alpha": a * 252, "a_lo": al, "a_hi": ah, "a_p": ap}
            print(f"| {name} | {cost:.2%} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['sharpe']:.2f} ({sl:.2f} a {shh:.2f}) | {s['dd']:.1%} | "
                  f"{b:.2f} | {a * 252:+.1%} ({al:+.1%} a {ah:+.1%}) | {ap:.0%} | {(r.mean() - spy.mean()) * 252:+.1%} ({el:+.1%} a {eh:+.1%}) |")
    s = stats(spy)
    print(f"| SPY comprar y mantener | — | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | 1.00 | — | — | — |")
    years = sorted({dates[days[t]][:4] for t in T})
    print("\n### Por año (acumulado, costo 0.10%)\n")
    print("| Versión | " + " | ".join(years) + " |")
    print("|---|" + "---|" * len(years))
    series = {**{n: daily(v, COSTS[0]) for n, v in res.items()}, "SPY": spy}
    for name, r in series.items():
        cells = []
        for y in years:
            rr = [x for x, t in zip(r, T) if dates[days[t]][:4] == y]
            cells.append(f"{np.prod(1 + np.array(rr)) - 1:+.1%}" if rr else "—")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print(f"\nAcciones 'grandes' por lista (promedio): " +
          ", ".join(f"{n[:2]} {np.mean([x['big'] for x in v]):.1f}" for n, v in res.items()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3)
    a = ap.parse_args()
    p, today_uni, n_inactive = load(a.years)
    rng = np.random.default_rng(7)
    out = {}
    for mode, title, uni in (("hoy", "Universo de hoy (como research.py)", today_uni),
                             ("dia", "Universo de cada día (con deslistadas)", [s for s in p.syms if s not in INDEXES])):
        S, days, spy_cc = build(p, uni, mode)
        res = walk_forward(S, len(days))
        out[mode] = report(title, res, days, p.dates, spy_cc, rng)
    print("\n## Comparación rápida (costo 0.10%)\n")
    print("| Versión | Acumulado hoy | Acumulado cada día | Alfa anual hoy | Alfa anual cada día | Beta cada día |")
    print("|---|---|---|---|---|---|")
    for name, *_ in VARIANTS:
        h, d = out["hoy"][(name, COSTS[0])], out["dia"][(name, COSTS[0])]
        print(f"| {name} | {h['cum']:+.1%} | {d['cum']:+.1%} | {h['alpha']:+.1%} | {d['alpha']:+.1%} | {d['beta']:.2f} |")
    print(f"\nNotas: el panel tiene {n_inactive} acciones deslistadas que alguna vez fueron líquidas. Si una acción deja de "
          "cotizar mientras se tiene, su último tramo se pierde (sale del promedio); en quiebras eso favorece un poco a la "
          "estrategia. La prueba no incluye el filtro de reportes ni el límite 'No comprar arriba de' de producción. "
          "Beta y alfa salen de la regresión de los retornos diarios contra el SPY cierre a cierre.")


if __name__ == "__main__":
    main()
