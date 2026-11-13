"""Investigación: ¿una canasta de "oportunidades de crecimiento" con reglas fijas le gana al S&P 500 y al Nasdaq?

Misma prueba honesta que research_buffett.py (se reusan sus funciones):
- Fundamentales point-in-time de SEC EDGAR (sólo 10-K publicados ANTES de cada rebalanceo).
- Universo de cada fecha: las 1,000 acciones con más dólares negociados (incluye deslistadas), precio >= $5.
- Precios Alpaca SIP desde 2016, con dividendos. Rebalanceo trimestral, 15 empresas con el mismo peso,
  costo 0.10% por peso que cambia de manos. Lugares vacíos van a SPY.

Reglas FIJADAS ANTES DE VER RESULTADOS (2026-11-13). No se ajustan después:
  Crecimiento (versión G):
  1. Ventas: crecimiento anual compuesto de 3 años >= 15% y el último año >= 10%.
  2. Rentable hoy: utilidad neta > 0 y flujo libre (operativo - capex) > 0 en el último año.
  3. Utilidad por acción (diluida) de 3 años creciendo (> 0% al año).
  4. Sin dilución fuerte: acciones diluidas +10% o menos en 3 años.
  5. 10-K reciente (del año pasado o antepasado).
  Orden: mayor crecimiento de ventas de 3 años; se toman las 15 primeras.
  Crecimiento a precio razonable (versión GARP): reglas 1-5 y además PEG <= 2
  (P/U del último año ÷ crecimiento de la utilidad por acción de 3 años en puntos), orden por PEG más bajo.
  Meseta: crecimiento de ventas mínimo 10% / 15% / 20% en la versión G.

Se compara contra SPY, QQQ (Nasdaq 100) y VUG (Vanguard Growth), y el universo con pesos iguales.

Uso:  python research_growth.py [--years 11] [--top 15]
"""
from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import requests

from qx import config as C
from qx.data_alpaca import Alpaca, last_completed_session
from research_buffett import (CACHE, FACTS_URL, TICKERS_URL, UA, UNIVERSE_N, all_assets, bars, boot_alpha, download,
                              history, index_facts, load_fundamentals, log, matrix, norm_name, ols, pit, shares_now,
                              simulate, stats, NY)

BENCH = ("SPY", "QQQ", "VUG")


def cagr(a, b, n):
    return (b / a) ** (1 / n) - 1 if a > 0 and b > 0 and n > 0 else None


def evaluate(f: dict, day: str, gmin=0.15):
    """Reglas 1-5. Devuelve (pasa, datos)."""
    rev_k, rev = history(f["rev"], day, 4)
    if len(rev) < 4 or rev_k[-1] < str(int(day[:4]) - 2) or int(rev_k[-1]) - int(rev_k[0]) != 3:
        return False, None
    g3 = cagr(rev[0], rev[-1], 3)
    g1 = cagr(rev[-2], rev[-1], 1)
    if g3 is None or g1 is None or g3 < gmin or g1 < 0.10:
        return False, None
    ni = pit(f["ni"], day)
    fy = rev_k[-1]
    if ni.get(fy, -1) <= 0:
        return False, None
    ocf, cap = pit(f["ocf"], day), pit(f["capex"], day)
    if fy not in ocf or ocf[fy] - cap.get(fy, 0.0) <= 0:
        return False, None
    sh = pit(f["sh"], day)
    y0 = str(int(fy) - 3)
    if not (sh.get(fy, 0) > 0 and sh.get(y0, 0) > 0 and y0 in ni):
        return False, None
    if sh[fy] / sh[y0] > 1.10:
        return False, None
    e0, e1 = ni[y0] / sh[y0], ni[fy] / sh[fy]
    eg = cagr(e0, e1, 3)
    if eg is None or eg <= 0:
        return False, None
    return True, {"g3": g3, "eg": eg, "ni": ni[fy], "fy": fy}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=11)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    if args.years <= 0:
        args.years = 11
    N = args.top
    rng = np.random.default_rng(7)

    log("bajando companyfacts.zip de EDGAR (≈1 GB)…")
    zpath = download(FACTS_URL, f"{CACHE}/companyfacts.zip")
    tick = requests.get(TICKERS_URL, headers=UA, timeout=60).json()
    cik_by_ticker = {v["ticker"].upper().replace("-", "."): int(v["cik_str"]) for v in tick.values()}
    names = index_facts(zpath)
    by_name = {}
    for cik, nm in names.items():
        by_name.setdefault(norm_name(nm), set()).add(cik)
    log(f"EDGAR: {len(names):,} empresas con XBRL")

    a = Alpaca()
    now = dt.datetime.now(NY)
    today = now.date()
    cal = a.calendar(today - dt.timedelta(days=40), today + dt.timedelta(days=10))
    last = last_completed_session(cal, now)
    start = today - dt.timedelta(days=int(365 * args.years))
    assets = all_assets(a)
    syms = sorted(assets)
    mon = bars(a, syms, start, "1Month", "raw", chunk=200)
    months = sorted({t for r in mon.values() for t in r["t"]})
    M = matrix(mon, months, syms, "c") * matrix(mon, months, syms, "v")
    P = matrix(mon, months, syms, "c")
    M[~(P >= C.MIN_PRICE)] = np.nan
    top = int(UNIVERSE_N * 1.6)
    cand = set()
    for j in range(len(months)):
        col = np.where(np.isfinite(M[:, j]), M[:, j], -np.inf)
        cand.update(int(k) for k in np.argsort(-col)[:top] if np.isfinite(col[k]))
    cand = sorted(syms[k] for k in cand)
    cik = {}
    for s in cand:
        c = cik_by_ticker.get(s) if assets[s]["status"] == "active" else None
        if c and c in names:
            cik[s] = c
            continue
        hits = by_name.get(norm_name(assets[s]["name"]), set())
        if len(hits) == 1:
            cik[s] = next(iter(hits))
    fund = load_fundamentals(zpath, sorted(set(cik.values())))
    log(f"candidatas {len(cand):,}, con CIK {len(cik):,}, con fundamentales {len(fund):,}")

    S = sorted(set(cand) | set(BENCH))
    adj = bars(a, S, start, "1Day", "all")
    raw = bars(a, S, start, "1Day", "raw")
    dates = [t for t in adj["SPY"]["t"] if t <= last]
    row = {s: k for k, s in enumerate(S)}
    dates_np = np.array(dates)
    A = matrix(adj, dates, S, "c")
    Pr = matrix(raw, dates, S, "c")
    V = matrix(raw, dates, S, "v")
    with np.errstate(invalid="ignore", divide="ignore"):
        R = A[:, 1:] / A[:, :-1] - 1
    R = np.concatenate([np.full((len(S), 1), np.nan), R], axis=1)
    R[~np.isfinite(R)] = np.nan
    R[np.abs(R) > 1.5] = np.nan

    reb = [k for k, d in enumerate(dates) if k >= 64 and d[5:7] in ("01", "04", "07", "10") and dates[k - 1][5:7] != d[5:7]]
    log(f"{len(reb)} rebalanceos ({dates[reb[0]]} a {dates[reb[-1]]})")
    dv = np.where(Pr >= C.MIN_PRICE, Pr * V, np.nan)
    stock_rows = np.array([row[s] for s in S if s not in BENCH])
    VARS = [("G10", 0.10, False), ("G15", 0.15, False), ("G20", 0.20, False), ("GARP", 0.15, True)]
    picks = {v[0]: [] for v in VARS}
    picks["EW"] = []
    for i in reb:
        day, sig = dates[i], i - 1
        win = dv[stock_rows, max(0, sig - 62):sig + 1]
        cnt = np.isfinite(win).sum(axis=1)
        with np.errstate(invalid="ignore"):
            avg = np.where(cnt >= 40, np.nanmean(win, axis=1), np.nan)
        avg = np.where(np.isfinite(Pr[stock_rows, sig]), avg, np.nan)
        order = np.argsort(-np.where(np.isfinite(avg), avg, -np.inf))
        uni = [int(stock_rows[k]) for k in order[:UNIVERSE_N] if np.isfinite(avg[k])]
        picks["EW"].append(uni)
        has = [r for r in uni if cik.get(S[r]) in fund]
        for name, gmin, garp in VARS:
            ok = []
            for r in has:
                f = fund[cik[S[r]]]
                p, info = evaluate(f, day, gmin)
                if not p:
                    continue
                if not garp:
                    ok.append((-info["g3"], r))
                    continue
                sn = shares_now(f, day)
                if not sn:
                    continue
                shares, filed = sn
                jf = max(0, min(sig, int(np.searchsorted(dates_np, filed))))
                fa, fr, sa, sr = A[r, jf], Pr[r, jf], A[r, sig], Pr[r, sig]
                if not all(np.isfinite([fa, fr, sa, sr])) or min(fa, fr, sa, sr) <= 0:
                    continue
                mcap = shares / ((sa / sr) / (fa / fr)) * sr
                peg = (mcap / info["ni"]) / (info["eg"] * 100)
                if 0 < peg <= 2:
                    ok.append((peg, r))
            ok.sort()
            picks[name].append([r for _, r in ok[:N]])
        if len(picks["G15"]) % 8 == 1:
            log(f"  {day}: universo {len(uni)}, G15 {len(picks['G15'][-1])}: {' '.join(S[r] for r in picks['G15'][-1])}")

    # ---------- ideas de hoy: las mismas reglas con lo publicado hasta la última sesión
    def pick_at(i, gmin, garp):
        day, sig = dates[i], i
        win = dv[stock_rows, max(0, sig - 62):sig + 1]
        cnt = np.isfinite(win).sum(axis=1)
        with np.errstate(invalid="ignore"):
            avg = np.where(cnt >= 40, np.nanmean(win, axis=1), np.nan)
        avg = np.where(np.isfinite(Pr[stock_rows, sig]), avg, np.nan)
        order = np.argsort(-np.where(np.isfinite(avg), avg, -np.inf))
        uni = [int(stock_rows[k]) for k in order[:UNIVERSE_N] if np.isfinite(avg[k])]
        out = []
        for r in uni:
            if cik.get(S[r]) not in fund:
                continue
            f = fund[cik[S[r]]]
            p, info = evaluate(f, day + "~", gmin)      # "~" > cualquier fecha del mismo día: incluye lo publicado hoy
            if not p:
                continue
            peg = None
            sn = shares_now(f, day + "~")
            if sn:
                shares, filed = sn
                jf = max(0, min(sig, int(np.searchsorted(dates_np, filed))))
                fa, fr, sa, sr = A[r, jf], Pr[r, jf], A[r, sig], Pr[r, sig]
                if all(np.isfinite([fa, fr, sa, sr])) and min(fa, fr, sa, sr) > 0:
                    mcap = shares / ((sa / sr) / (fa / fr)) * sr
                    peg = (mcap / info["ni"]) / (info["eg"] * 100)
            if garp and not (peg is not None and 0 < peg <= 2):
                continue
            out.append({"symbol": S[r], "name": assets.get(S[r], {}).get("name", ""), "revG3": round(info["g3"], 4),
                        "epsG3": round(info["eg"], 4), "peg": round(peg, 2) if peg else None, "fy": info["fy"]})
        out.sort(key=(lambda x: x["peg"]) if garp else (lambda x: -x["revG3"]))
        return out[:N]
    ideas = {"asOf": dates[-1], "growth": pick_at(len(dates) - 1, 0.15, False), "garp": pick_at(len(dates) - 1, 0.15, True)}

    spy_row, t0 = row["SPY"], reb[0]
    series = {}
    for name in [v[0] for v in VARS]:
        r, to = simulate(R, picks[name], reb, spy_row, N)
        series[name] = (r[t0 + 1:], to, np.mean([len(x) for x in picks[name]]))
    r, to = simulate(R, picks["EW"], reb, spy_row, UNIVERSE_N)
    series["EW"] = (r[t0 + 1:], to, np.mean([len(x) for x in picks["EW"]]))
    for b in BENCH:
        series[b] = (np.nan_to_num(R[row[b], t0 + 1:]), [0.0], 1)
    spy, qqq = series["SPY"][0], series["QQQ"][0]
    D = dates[t0 + 1:]
    label = {"G10": "G. Crecimiento, ventas ≥10%", "G15": "G. Crecimiento, ventas ≥15% (principal)",
             "G20": "G. Crecimiento, ventas ≥20%", "GARP": "GARP. Crecimiento + PEG ≤ 2",
             "EW": f"Universo ({UNIVERSE_N}) pesos iguales", "SPY": "SPY (S&P 500)", "QQQ": "QQQ (Nasdaq 100)",
             "VUG": "VUG (Vanguard Growth)"}

    print(f"\n# Backtest crecimiento: {D[0]} a {D[-1]} ({len(D)} sesiones, {len(reb)} rebalanceos trimestrales)\n")
    print(f"{N} empresas, mismo peso, costo 0.10% por peso que cambia, lugares vacíos en SPY. Universo point-in-time.\n")
    print("| Versión | Acumulado | Anual | Volatilidad | Sharpe | Peor caída | Beta | Alfa vs SPY (IC 95%) | Prob. alfa > 0 | Ventaja vs SPY | Ventaja vs QQQ | Empresas prom. | Rotación anual |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, (r, to, n) in series.items():
        s = stats(r)
        a_, b_ = ols(r, spy)
        if k == "SPY":
            print(f"| {label[k]} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['vol']:.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | 1.00 | — | — | — | {(r.mean() - qqq.mean()) * 252:+.1%} | — | — |")
            continue
        (al, ah, ap_), _ = boot_alpha(r, spy, rng)
        print(f"| {label[k]} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['vol']:.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | {b_:.2f} | "
              f"{a_ * 252:+.1%} ({al:+.1%} a {ah:+.1%}) | {ap_:.0%} | {(r.mean() - spy.mean()) * 252:+.1%} | "
              f"{(r.mean() - qqq.mean()) * 252:+.1%} | {n:.0f} | {np.sum(to) / (len(D) / 252):.0%} |")

    years = sorted({d[:4] for d in D})
    print("\n## Por año\n")
    print("| Versión | " + " | ".join(years) + " |")
    print("|---|" + "---|" * len(years))
    for k, (r, _, _) in series.items():
        cells = []
        for y in years:
            rr = np.array([x for x, d in zip(r, D) if d[:4] == y])
            cells.append(f"{np.prod(1 + rr) - 1:+.1%}" if len(rr) else "—")
        print(f"| {label[k]} | " + " | ".join(cells) + " |")

    cut3 = next(j for j, d in enumerate(D) if d >= (today - dt.timedelta(days=365 * 3)).isoformat())
    print(f"\n## Últimos 3 años ({D[cut3]} a {D[-1]})\n")
    print("| Versión | Acumulado | Sharpe | Peor caída |")
    print("|---|---|---|---|")
    for k, (r, _, _) in series.items():
        s = stats(r[cut3:])
        print(f"| {label[k]} | {s['cum']:+.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} |")

    print("\n## Canastas\n")
    for j in (0, len(reb) // 2, len(reb) - 1):
        print(f"- {dates[reb[j]]} G15: {' '.join(S[r] for r in picks['G15'][j]) or '(ninguna)'}")
        print(f"- {dates[reb[j]]} GARP: {' '.join(S[r] for r in picks['GARP'][j]) or '(ninguna)'}")
    summ = {}
    for k in ("G15", "GARP", "SPY", "QQQ", "VUG"):
        st = stats(series[k][0])
        summ[k] = {"ann": round(st["ann"], 4), "dd": round(st["dd"], 4), "sharpe": round(st["sharpe"], 2), "cum": round(st["cum"], 4)}
    yrs = {}
    for k in ("G15", "GARP", "SPY", "QQQ"):
        r = series[k][0]
        yrs[k] = {y: round(float(np.prod(1 + np.array([x for x, d in zip(r, D) if d[:4] == y])) - 1), 4) for y in years}
    ideas.update({"from": D[0], "to": D[-1], "backtest": summ, "byYear": yrs,
                  "rules": "Ventas +15% al año (3 años) y +10% el último; utilidad y flujo libre positivos; utilidad por acción creciendo; acciones +10% o menos en 3 años. GARP: además PEG <= 2."})
    import json
    with open("growth_ideas.json", "w") as fh:
        json.dump(ideas, fh, ensure_ascii=False, indent=1)
    print("\nIDEAS_JSON " + json.dumps(ideas, ensure_ascii=False))
    print("\nNotas: reglas fijadas antes de correr; la meseta (10/15/20%) muestra si el resultado depende del número exacto. "
          "Alpaca no tiene precios antes de 2016. Nada de esto es asesoría financiera.")


if __name__ == "__main__":
    main()
