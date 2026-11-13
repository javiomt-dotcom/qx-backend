"""¿Algún portafolio le gana al S&P 500 de forma creíble? (investigación, no producción)

Dos pruebas, ambas con rendimientos mensuales:

A. Historia larga (1963 a hoy) con los portafolios públicos de Kenneth French
   (Dartmouth). Sólo empresas GRANDES para que sea invertible. Factores: valor,
   rentabilidad (calidad), inversión conservadora, momentum, baja volatilidad,
   valor pequeño, combinación multifactor y seguimiento de tendencia (media de
   10 meses). Se descuenta un costo anual por rotación + comisión tipo ETF.
   Se mide antes y DESPUÉS de que cada factor se publicó (fuera de muestra real),
   los últimos 20 y 10 años, y qué % de ventanas de 10 años le ganaron al mercado.

B. Fondos reales (ETFs y fondos DFA/AQR) desde que existen, contra SPY en el
   mismo periodo: ya traen comisiones y costos reales, y no hay sesgo de
   supervivencia del índice.

Para cada uno: rendimiento anual, volatilidad, Sharpe, peor caída, beta y alfa
CAPM con su estadístico t (Newey-West, 12 rezagos). Un portafolio que gana sólo
por tener beta mayor a 1 NO es mejor: es más riesgo.

Uso: python research_portfolios.py   (necesita internet: Dartmouth y Yahoo)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile

import numpy as np
import requests

UA = {"User-Agent": "Mozilla/5.0 (QX research)"}
FF = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- datos French
def ff_monthly(name: str) -> dict[str, dict[int, float]]:
    """Primera tabla mensual de un CSV de French -> {columna: {yyyymm: rend decimal}}"""
    for i in range(4):
        try:
            r = requests.get(FF + name + "_CSV.zip", headers=UA, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:  # noqa: BLE001
            log(f"{name}: intento {i + 1} falló {e}")
            time.sleep(5 * (i + 1))
    else:
        raise RuntimeError(f"no pude bajar {name}")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    text = z.read(z.namelist()[0]).decode("latin-1")
    lines = [l.strip() for l in text.splitlines()]
    out: dict[str, dict[int, float]] = {}
    header = None
    started = False
    prev = ""
    for l in lines:
        parts = [p.strip() for p in l.split(",")]
        is_row = len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 6
        if is_row:
            if not started:
                header = [h.strip() for h in prev.split(",")][1:]
                out = {h: {} for h in header}
                started = True
            ym = int(parts[0])
            for h, v in zip(header, parts[1:]):
                try:
                    x = float(v)
                except ValueError:
                    continue
                if x > -99:
                    out[h][ym] = x / 100.0
        elif started:
            break
        if l:
            prev = l
    log(f"{name}: columnas {header}")
    return out


def col(d: dict, *keys) -> dict[int, float]:
    norm = {k.replace(" ", "").upper(): k for k in d}
    for k in keys:
        kk = k.replace(" ", "").upper()
        if kk in norm:
            return d[norm[kk]]
    raise KeyError(f"{keys} no está en {list(d)}")


# ---------------------------------------------------------------- Yahoo
def yahoo_monthly(sym: str) -> dict[int, float]:
    """Rendimientos mensuales totales (precio ajustado por dividendos y splits)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"period1": 0, "period2": int(time.time()), "interval": "1mo", "events": "div,splits",
              "includeAdjustedClose": "true"}
    for i in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res["timestamp"]
            adj = res["indicators"]["adjclose"][0]["adjclose"]
            px = {}
            for t, a in zip(ts, adj):
                if a is None:
                    continue
                g = time.gmtime(t)
                px[g.tm_year * 100 + g.tm_mon] = a
            yms = sorted(px)
            # el último mes puede estar incompleto: lo quitamos
            cur = time.gmtime()
            if yms and yms[-1] == cur.tm_year * 100 + cur.tm_mon:
                yms = yms[:-1]
            out = {}
            for a, b in zip(yms[:-1], yms[1:]):
                out[b] = px[b] / px[a] - 1
            return out
        except Exception as e:  # noqa: BLE001
            log(f"{sym}: intento {i + 1} falló {e}")
            time.sleep(3 * (i + 1))
    return stooq_monthly(sym)


def stooq_monthly(sym: str) -> dict[int, float]:
    """Respaldo si Yahoo falla (Stooq ajusta por splits y dividendos). No sirve para fondos mutuos."""
    try:
        r = requests.get(f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=m", headers=UA, timeout=30)
        rows = [l.split(",") for l in r.text.strip().splitlines()[1:]]
        px = {int(d[:4]) * 100 + int(d[5:7]): float(c) for d, _o, _h, _l, c, *_ in rows}
    except Exception as e:  # noqa: BLE001
        log(f"{sym}: Stooq falló {e}")
        return {}
    yms = sorted(px)
    cur = time.gmtime()
    if yms and yms[-1] == cur.tm_year * 100 + cur.tm_mon:
        yms = yms[:-1]
    log(f"{sym}: usando Stooq")
    return {b: px[b] / px[a] - 1 for a, b in zip(yms[:-1], yms[1:])}


# ---------------------------------------------------------------- estadística
def next_ym(ym: int) -> int:
    y, m = divmod(ym, 100)
    return ym + 1 if m < 12 else (y + 1) * 100 + 1


def ym_range(a: int, b: int):
    while a <= b:
        yield a
        a = next_ym(a)


def nw_t(x: np.ndarray, lags: int = 12) -> float:
    """t de la media con errores Newey-West."""
    n = len(x)
    if n < 24:
        return float("nan")
    if x.std() < 1e-7:
        return float("nan")
    e = x - x.mean()
    s = e @ e / n
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        s += 2 * w * (e[l:] @ e[:-l]) / n
    return float(x.mean() / np.sqrt(s / n)) if s > 1e-12 else float("nan")


def capm(r: np.ndarray, m: np.ndarray, rf: np.ndarray):
    """alfa anual, t de alfa (NW), beta"""
    y, x = r - rf, m - rf
    X = np.column_stack([np.ones_like(x), x])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ b
    # t del alfa: media de (y - beta x) con NW
    t = nw_t(y - b[1] * x)
    return float((1 + b[0]) ** 12 - 1), t, float(b[1]), resid


def stats(r: np.ndarray, rf: np.ndarray, m: np.ndarray) -> dict:
    n = len(r)
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (12 / n) - 1
    vol = r.std(ddof=1) * np.sqrt(12)
    ex = r - rf
    sharpe = ex.mean() / ex.std(ddof=1) * np.sqrt(12) if ex.std() > 0 else float("nan")
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    a, t, beta, resid = capm(r, m, rf)
    active = r - m
    te = active.std(ddof=1) * np.sqrt(12)
    mcagr = np.cumprod(1 + m)[-1] ** (12 / n) - 1
    return dict(n=n, cagr=cagr, vol=vol, sharpe=sharpe, dd=dd, beta=beta, alpha=a, t=t,
                excess=cagr - mcagr, te=te, total=eq[-1] - 1, t_active=nw_t(active))


def rolling_win(r: np.ndarray, m: np.ndarray, rf: np.ndarray, win: int = 120):
    """% de ventanas de 10 años en que gana al mercado en rendimiento y en Sharpe."""
    if len(r) < win + 12:
        return float("nan"), float("nan")
    wins = swins = tot = 0
    for i in range(0, len(r) - win + 1):
        a, b, f = r[i:i + win], m[i:i + win], rf[i:i + win]
        ca, cb = np.prod(1 + a), np.prod(1 + b)
        sa = (a - f).mean() / (a - f).std()
        sb = (b - f).mean() / (b - f).std()
        wins += ca > cb
        swins += sa > sb
        tot += 1
    return wins / tot, swins / tot


def pct(x, d=1):
    return "—" if x is None or x != x else f"{x * 100:+.{d}f}%"


def num(x, d=2):
    return "—" if x is None or x != x else f"{x:.{d}f}"


def row(name, s, extra=""):
    return (f"| {name} | {pct(s['cagr'])} | {pct(s['excess'])} | {num(s['sharpe'])} | {pct(s['dd'], 0)} | "
            f"{num(s['beta'])} | {pct(s['alpha'])} | {num(s['t'], 1)} |{extra}")


HDR = ("| Estrategia | Anual | vs mercado | Sharpe | Peor caída | Beta | Alfa anual | t alfa |")
SEP = "|---|---|---|---|---|---|---|---|"


# ---------------------------------------------------------------- reglas
def trend(series: dict[int, float], rf: dict[int, float], months: list[int], look=10, switch_cost=0.001):
    """Invertido si el índice (rend. total) al cierre del mes anterior >= su media de `look` meses."""
    idx, lvl = [], 1.0
    for ym in months:
        lvl *= 1 + series[ym]
        idx.append(lvl)
    out, state = {}, None
    for i, ym in enumerate(months):
        if i < look:
            continue
        sma = np.mean(idx[i - look:i])
        inv = idx[i - 1] >= sma
        r = series[ym] if inv else rf[ym]
        if state is not None and inv != state:
            r -= switch_cost
        state = inv
        out[ym] = r
    return out


def mix(parts: dict[str, float], data: dict[str, dict[int, float]], months):
    out = {}
    for ym in months:
        if all(ym in data[k] for k in parts):
            out[ym] = sum(w * data[k][ym] for k, w in parts.items())
    return out


def levered(series, rf, months, lev=1.25, spread=0.005):
    return {ym: lev * series[ym] - (lev - 1) * (rf[ym] + spread / 12) for ym in months if ym in series}


# ---------------------------------------------------------------- A. French
PUB = {  # mes de publicación académica (lo de después es fuera de muestra)
    "Valor grandes": (199206, "Fama-French 1992"),
    "Rentabilidad grandes (calidad)": (201304, "Novy-Marx 2013"),
    "Inversión conservadora grandes": (201409, "Fama-French 2015 (borrador 2014)"),
    "Momentum grandes": (199303, "Jegadeesh-Titman 1993"),
    "Baja volatilidad": (200602, "Ang, Hodrick, Xing, Zhang 2006"),
    "Valor pequeñas": (199206, "Fama-French 1992"),
}
COST = {  # costo anual estimado: rotación + comisión tipo ETF
    "Valor grandes": 0.0035, "Rentabilidad grandes (calidad)": 0.0035, "Inversión conservadora grandes": 0.0035,
    "Momentum grandes": 0.0090, "Baja volatilidad": 0.0040, "Valor pequeñas": 0.0060,
}


def part_a(out_json):
    f3 = ff_monthly("F-F_Research_Data_Factors")
    bm = ff_monthly("6_Portfolios_2x3")
    op = ff_monthly("6_Portfolios_ME_OP_2x3")
    inv = ff_monthly("6_Portfolios_ME_INV_2x3")
    pr = ff_monthly("6_Portfolios_ME_Prior_12_2")
    var = ff_monthly("Portfolios_Formed_on_VAR")
    rf = col(f3, "RF")
    mrf = col(f3, "Mkt-RF")
    mkt = {k: mrf[k] + rf[k] for k in mrf}
    raw = {
        "Valor grandes": col(bm, "BIG HiBM"),
        "Rentabilidad grandes (calidad)": col(op, "BIG HiOP"),
        "Inversión conservadora grandes": col(inv, "BIG LoINV"),
        "Momentum grandes": col(pr, "BIG HiPRIOR"),
        "Baja volatilidad": col(var, "Lo 20"),
        "Valor pequeñas": col(bm, "SMALL HiBM"),
    }
    start = 196307
    end = min(max(v) for v in list(raw.values()) + [mkt])
    months = [ym for ym in ym_range(start, end) if all(ym in v for v in list(raw.values()) + [mkt, rf])]
    log(f"French: {months[0]}..{months[-1]} ({len(months)} meses)")
    data = {"Mercado EE. UU. (cap. ponderada)": {ym: mkt[ym] - 0.0003 / 12 for ym in months}}
    for k, v in raw.items():
        data[k] = {ym: v[ym] - COST[k] / 12 for ym in months}
    data["Multifactor grandes (valor+calidad+inversión+momentum)"] = mix(
        {k: 0.25 for k in ["Valor grandes", "Rentabilidad grandes (calidad)", "Inversión conservadora grandes",
                           "Momentum grandes"]}, data, months)
    data["Valor + momentum grandes 50/50"] = mix({"Valor grandes": 0.5, "Momentum grandes": 0.5}, data, months)
    data["Mercado con tendencia (media 10 meses)"] = trend(data["Mercado EE. UU. (cap. ponderada)"], rf, months)
    data["Multifactor con tendencia"] = trend(data["Multifactor grandes (valor+calidad+inversión+momentum)"], rf,
                                              months)
    data["Núcleo 70% mercado + 30% multifactor"] = mix(
        {"Mercado EE. UU. (cap. ponderada)": 0.7, "Multifactor grandes (valor+calidad+inversión+momentum)": 0.3},
        data, months)
    data["Mercado apalancado 1.25x (para comparar)"] = levered(data["Mercado EE. UU. (cap. ponderada)"], rf, months)
    M = "Mercado EE. UU. (cap. ponderada)"

    def table(title, a, b):
        print(f"\n### {title}\n")
        print(HDR + " Gana 10 años (rend / Sharpe) |")
        print(SEP + "---|")
        res = {}
        for k, s in data.items():
            ms = [ym for ym in months if a <= ym <= b and ym in s]
            if len(ms) < 36:
                continue
            r = np.array([s[ym] for ym in ms])
            m = np.array([data[M][ym] for ym in ms])
            f = np.array([rf[ym] for ym in ms])
            st = stats(r, f, m)
            w, sw = rolling_win(r, m, f)
            st["win10"], st["swin10"] = w, sw
            res[k] = st
            extra = "" if k == M else f" {pct(w, 0)} / {pct(sw, 0)} |"
            print(row(k, st, extra if k != M else " — |"))
        return res

    out_json["A"] = {}
    out_json["A"]["full"] = table(f"A1. Historia completa {months[0]}–{months[-1]} (costos incluidos)", months[0],
                                  months[-1])
    print("\n### A2. Sólo DESPUÉS de publicarse cada factor (prueba fuera de muestra)\n")
    print("| Factor | Publicado | Meses | Anual | Mercado mismo periodo | Sharpe vs mercado | Beta | Alfa anual | t alfa |")
    print("|---|---|---|---|---|---|---|---|---|")
    out_json["A"]["post"] = {}
    for k, (ym0, ref) in PUB.items():
        ms = [ym for ym in months if ym > ym0]
        r = np.array([data[k][ym] for ym in ms])
        m = np.array([data[M][ym] for ym in ms])
        f = np.array([rf[ym] for ym in ms])
        st, sm = stats(r, f, m), stats(m, f, m)
        msg = (f"| {k} | {ref} | {len(ms)} | {pct(st['cagr'])} | {pct(sm['cagr'])} | "
               f"{num(st['sharpe'])} vs {num(sm['sharpe'])} | {num(st['beta'])} | {pct(st['alpha'])} | {num(st['t'], 1)} |")
        print(msg)
        pre = [ym for ym in months if ym <= ym0]
        st_pre = stats(np.array([data[k][ym] for ym in pre]), np.array([rf[ym] for ym in pre]),
                       np.array([data[M][ym] for ym in pre]))
        out_json["A"]["post"][k] = {"post": st, "pre": st_pre, "pub": ym0}
    print("\nAlfa antes vs después de publicarse:")
    for k, v in out_json["A"]["post"].items():
        print(f"- {k}: antes {pct(v['pre']['alpha'])} (t {num(v['pre']['t'], 1)}), después {pct(v['post']['alpha'])} (t {num(v['post']['t'], 1)})")
    last = months[-1]
    y20 = last - 2000
    y10 = last - 1000
    out_json["A"]["y20"] = table(f"A3. Últimos 20 años ({y20 + 1}–{last})", y20 + 1, last)
    out_json["A"]["y10"] = table(f"A4. Últimos 10 años ({y10 + 1}–{last})", y10 + 1, last)
    # por década: exceso anual de cada estrategia sobre el mercado
    print("\n### A5. Exceso anual sobre el mercado por década\n")
    decs = [(1963, 1969), (1970, 1979), (1980, 1989), (1990, 1999), (2000, 2009), (2010, 2019), (2020, 2026)]
    print("| Estrategia | " + " | ".join(f"{a}s" if a != 1963 else "1963-69" for a, _ in decs) + " |")
    print("|---|" + "---|" * len(decs))
    for k, s in data.items():
        if k == M:
            continue
        cells = []
        for a, b in decs:
            ms = [ym for ym in months if a * 100 <= ym <= b * 100 + 12 and ym in s]
            if len(ms) < 24:
                cells.append("—")
                continue
            r = np.prod([1 + s[ym] for ym in ms]) ** (12 / len(ms))
            m = np.prod([1 + data[M][ym] for ym in ms]) ** (12 / len(ms))
            cells.append(pct(r - m))
        print(f"| {k} | " + " | ".join(cells) + " |")
    return rf


# ---------------------------------------------------------------- B. fondos reales
FUNDS = {
    "RSP": "S&P 500 peso igual", "IVE": "S&P 500 valor", "IWD": "Russell 1000 valor", "VTV": "Vanguard valor",
    "IJS": "S&P 600 valor pequeñas", "VBR": "Vanguard valor pequeñas", "SPHQ": "S&P 500 calidad (Invesco)",
    "QUAL": "MSCI calidad (iShares)", "MTUM": "MSCI momentum (iShares)", "USMV": "MSCI mínima volatilidad",
    "SPLV": "S&P 500 baja volatilidad", "VLUE": "MSCI valor mejorado", "LRGF": "Multifactor EE. UU. (iShares)",
    "AVUV": "Avantis valor pequeñas", "DFSVX": "DFA valor pequeñas (fondo)", "DFLVX": "DFA valor grandes (fondo)",
    "AMOMX": "AQR momentum grandes (fondo)", "QQQ": "Nasdaq 100", "MOAT": "Morningstar ventaja competitiva",
    "SCHD": "Dividendos Schwab", "COWZ": "Flujo de caja libre (Pacer)", "VTI": "Todo el mercado EE. UU.",
    "AVLV": "Avantis valor grandes", "DGRW": "Dividendos con crecimiento (WisdomTree)",
    "DFEOX": "DFA US Core Equity 1 (fondo: tilt pequeñas/valor/rentabilidad)",
    "DFQTX": "DFA US Core Equity 2 (fondo: tilt más fuerte)", "AVUS": "Avantis todo EE. UU. (tilt rentabilidad/valor)",
    "JQUA": "JPMorgan calidad", "SPMO": "S&P 500 momentum (Invesco)", "VFMF": "Vanguard multifactor",
    "DFUSX": "DFA US Large Company (índice, control)",
}
OTHER = ["SPY", "AGG", "BND", "IEF", "GLD", "BIL", "VXUS", "VEA", "VWO"]


def part_b(rf_ff, out_json):
    px = {}
    for s in ["SPY"] + list(FUNDS) + OTHER:
        px[s] = yahoo_monthly(s)
        log(f"{s}: {len(px[s])} meses" + (f" desde {min(px[s])}" if px[s] else ""))
        time.sleep(0.6)
    spy = px["SPY"]
    if not spy:
        print("\n**Sin datos de Yahoo: se omite la parte B.**")
        return
    last_rf = rf_ff[max(rf_ff)]
    rf = {ym: rf_ff.get(ym, (px["BIL"].get(ym) if px.get("BIL") else None) or last_rf) for ym in spy}
    print("\n## B. Fondos reales contra SPY, cada uno desde que existe (comisiones reales incluidas)\n")
    print("| Fondo | Qué es | Desde | Anual | SPY mismo periodo | Sharpe vs SPY | Beta | Alfa anual | t alfa | Peor caída vs SPY |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    out_json["B"] = {}
    for s, name in FUNDS.items():
        d = px.get(s) or {}
        ms = sorted(ym for ym in d if ym in spy)
        if len(ms) < 36:
            print(f"| {s} | {name} | sin datos | | | | | | | |")
            continue
        r = np.array([d[ym] for ym in ms])
        m = np.array([spy[ym] for ym in ms])
        f = np.array([rf[ym] for ym in ms])
        st, sm = stats(r, f, m), stats(m, f, m)
        w, sw = rolling_win(r, m, f, 60)
        st["win5"], st["swin5"] = w, sw
        out_json["B"][s] = {"fund": st, "spy": sm, "from": ms[0]}
        print(f"| {s} | {name} | {ms[0] // 100}-{ms[0] % 100:02d} | {pct(st['cagr'])} | {pct(sm['cagr'])} | "
              f"{num(st['sharpe'])} vs {num(sm['sharpe'])} | {num(st['beta'])} | {pct(st['alpha'])} | {num(st['t'], 1)} | "
              f"{pct(st['dd'], 0)} vs {pct(sm['dd'], 0)} |")
    wins = [k for k, v in out_json["B"].items() if v["fund"]["cagr"] > v["spy"]["cagr"]]
    print(f"\nLe ganaron a SPY en rendimiento: {len(wins)} de {len(out_json['B'])} ({', '.join(wins)}).")
    sig = [k for k, v in out_json["B"].items() if v["fund"]["t"] > 2]
    print(f"Con alfa significativo (t > 2): {', '.join(sig) or 'ninguno'}.")

    # combinaciones con ETFs desde 2013-08
    print("\n### B2. Combinaciones con ETFs (rebalanceo mensual)\n")
    print(HDR)
    print(SEP)
    combos = {
        "SPY": {"SPY": 1.0},
        "4 factores MSCI (valor, momentum, calidad, mín. vol.) 25% c/u": {"VLUE": .25, "MTUM": .25, "QUAL": .25, "USMV": .25},
        "70% SPY + 30% 4 factores": {"SPY": .7, "VLUE": .075, "MTUM": .075, "QUAL": .075, "USMV": .075},
        "SPY peso igual (RSP)": {"RSP": 1.0},
        "80% SPY + 20% valor pequeñas (AVUV/VBR)": {"SPY": .8, "VBR": .2},
        "Calidad + momentum (QUAL/MTUM 50/50)": {"QUAL": .5, "MTUM": .5},
    }
    common = sorted(set.intersection(*[set(px[s]) for s in ["SPY", "VLUE", "MTUM", "QUAL", "USMV", "RSP", "VBR"]]))
    out_json["B2"] = {}
    for name, w in combos.items():
        ms = [ym for ym in common if all(ym in px[s] for s in w)]
        r = np.array([sum(px[s][ym] * x for s, x in w.items()) for ym in ms])
        m = np.array([spy[ym] for ym in ms])
        f = np.array([rf[ym] for ym in ms])
        st = stats(r, f, m)
        out_json["B2"][name] = st
        print(row(name, st))
    print(f"\nPeriodo: {common[0]}–{common[-1]}")

    # tendencia sobre SPY real
    print("\n### B3. SPY con regla de tendencia (media 10 meses, a T-bills si cae debajo)\n")
    print(HDR)
    print(SEP)
    ms = sorted(spy)
    tr = trend(spy, rf, ms)
    ms2 = sorted(tr)
    r = np.array([tr[ym] for ym in ms2])
    m = np.array([spy[ym] for ym in ms2])
    f = np.array([rf[ym] for ym in ms2])
    print(row("SPY", stats(m, f, m)))
    st = stats(r, f, m)
    out_json["B3"] = st
    print(row("SPY con tendencia", st))
    print(f"\nPeriodo: {ms2[0]}–{ms2[-1]}")

    # mismo riesgo: portafolios con bonos contra su referencia justa
    print("\n### B4. Por qué un perfil con bonos no debe medirse contra el S&P 500 solo\n")
    print("| Mezcla | Anual | Volatilidad | Peor caída | Sharpe |")
    print("|---|---|---|---|---|")
    bond = "AGG"
    ms = sorted(ym for ym in spy if ym in px[bond])
    out_json["B4"] = {}
    for e in [1.0, 0.8, 0.6, 0.4]:
        r = np.array([e * spy[ym] + (1 - e) * px[bond][ym] for ym in ms])
        f = np.array([rf[ym] for ym in ms])
        st = stats(r, f, np.array([spy[ym] for ym in ms]))
        out_json["B4"][str(e)] = st
        print(f"| {int(e * 100)}% SPY / {int((1 - e) * 100)}% bonos (AGG) | {pct(st['cagr'])} | {pct(st['vol'])} | {pct(st['dd'], 0)} | {num(st['sharpe'])} |")
    print(f"\nPeriodo: {ms[0]}–{ms[-1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="0", help="sin uso; compatibilidad con el workflow")
    ap.add_argument("--json", default="research_portfolios.json")
    a = ap.parse_args()
    out = {}
    print("# ¿Qué portafolio le gana al S&P 500? Investigación honesta\n")
    print("Datos: Kenneth French Data Library (Dartmouth) y precios ajustados de Yahoo. Nada de esto es asesoría financiera.")
    print("Costos A: mercado 0.03%/año; valor, calidad e inversión 0.35%; baja vol. 0.40%; valor pequeñas 0.60%; momentum 0.90%; tendencia 0.1% por cambio.")
    print("t alfa > 2 es la regla común; como probamos ~12 ideas, lo exigente es t > 2.8.\n")
    print("## A. Historia larga con portafolios de French (empresas grandes)")
    rf = part_a(out)
    part_b(rf, out)
    with open(a.json, "w") as fh:
        json.dump(out, fh, default=float)
    log("listo")


if __name__ == "__main__":
    main()
