"""Investigación: ¿una canasta de calidad "estilo Buffett" le gana al S&P 500?

Prueba honesta, con datos como se veían en su momento:

- Fundamentales: SEC EDGAR (archivo masivo companyfacts.zip, XBRL de 10-K). Cada número trae la fecha en que se
  publicó; en cada rebalanceo sólo se usan reportes publicados ANTES de ese día (con la versión más reciente que
  existía entonces, así que una corrección posterior no se filtra hacia atrás).
- Universo de cada fecha: las 1,000 acciones con más dólares negociados (promedio de 63 sesiones, precio >= $5)
  ese día, entre todas las de Alpaca, activas y deslistadas.
- Precios: Alpaca (SIP desde 2016). Rendimientos con dividendos ("all"); valuación con precio sin ajustar.
- Rebalanceo trimestral (primera sesión de enero, abril, julio y octubre), 15 empresas con el mismo peso,
  costo de 0.10% por peso que cambia de manos. Lugares vacíos van a SPY.

Reglas (fijadas antes de ver resultados, ver el documento "Portafolios a la medida estilo Buffett"):
1. ROE >= 15% en al menos 80% de los años disponibles (mínimo 5, máximo 10) y en el último año; capital > 0.
2. Sin pérdidas en los últimos 5 años.
3. Deuda de largo plazo <= 3 veces la utilidad neta del último año.
4. Ganancias del dueño (flujo operativo - capex) positivas en al menos 80% de los años y en el último.
5. Acciones diluidas sin crecer más de 5% en 5 años.
6. (Versión B) Margen de seguridad: valor de mercado <= 75% del valor intrínseco (flujo descontado conservador).

Se comparan contra SPY, QUAL (calidad iShares), MOAT (VanEck Wide Moat) y el mismo universo con pesos iguales.
Prueba de "meseta": ROE 12/15/18% y margen de seguridad 15/25/35%.

Uso:  python research_buffett.py [--years 11] [--top 15]
Necesita ALPACA_KEY_ID / ALPACA_SECRET_KEY. EDGAR no pide llave (sólo un User-Agent).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
import zipfile
from zoneinfo import ZoneInfo

import numpy as np
import requests

from qx import config as C
from qx.data_alpaca import Alpaca, DATA, ETF_WORDS, last_completed_session

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "research-script contact@example.com", "Accept-Encoding": "gzip, deflate"}
FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE = os.environ.get("QX_CACHE", ".cache_buffett")
TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
BENCH = ("SPY", "QUAL", "MOAT")
UNIVERSE_N = 1000
COST = 0.001
BLOCK = 21
BOOT = 2000

# conceptos us-gaap (el primero que exista gana)
CONCEPTS = {
    "ni": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "eq": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "ltd": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
    "sh": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}
INSTANT = {"eq", "ltd"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ================================================================ EDGAR

def download(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for k in range(5):
        try:
            with requests.get(url, headers=UA, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(path + ".part", "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.replace(path + ".part", path)
            return path
        except Exception as e:
            log(f"  descarga falló ({e}); reintento")
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"no se pudo bajar {url}")


STOP = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|L P|HOLDINGS?|GROUP|"
                  r"THE|NV|N V|SA|S A|AG|SE|COMMON|STOCK|SHARES?|CLASS [A-Z]|CL [A-Z]|ORDINARY|NEW|DEL|DE|"
                  r"AMERICAN DEPOSITARY|ADS|ADR|REPRESENTING|EACH|PAR VALUE|USD|\d[\d.]*)\b")


def norm_name(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper().replace("&", " AND "))
    s = STOP.sub(" ", s)
    return " ".join(s.split())


def index_facts(zpath):
    """{cik: entityName} leyendo sólo el principio de cada archivo del zip."""
    names = {}
    pat = re.compile(rb'"entityName"\s*:\s*"((?:[^"\\]|\\.)*)"')
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            m = re.match(r"CIK(\d{10})\.json$", info.filename)
            if not m:
                continue
            with z.open(info) as f:
                head = f.read(600)
            mm = pat.search(head)
            if mm:
                names[int(m.group(1))] = json.loads(b'"' + mm.group(1) + b'"')
    return names


def annual(facts: dict, key: str):
    """Lista [(año_fiscal 'AAAA', valor, publicado)] de los 10-K para el primer concepto con datos."""
    gaap = facts.get("us-gaap", {})
    for concept in CONCEPTS[key]:
        node = gaap.get(concept)
        if not node:
            continue
        units = node.get("units", {})
        vals = units.get("USD") or units.get("shares") or next(iter(units.values()), [])
        out = []
        for x in vals:
            if not str(x.get("form", "")).startswith("10-K") or "filed" not in x or "end" not in x:
                continue
            end = dt.date.fromisoformat(x["end"])
            if key not in INSTANT:
                if "start" not in x:
                    continue
                days = (end - dt.date.fromisoformat(x["start"])).days
                if not 340 <= days <= 390:
                    continue
            fy = str((end - dt.timedelta(days=180)).year)    # año fiscal; aguanta años de 52/53 semanas
            out.append((fy, float(x["val"]), x["filed"]))
        if out:
            out.sort(key=lambda r: (r[2], r[0]))
            return out
    return []


def cover_shares(facts: dict):
    """Acciones en circulación de la portada (10-K y 10-Q): [(fecha, valor, publicado)]."""
    node = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not node:
        return []
    out = [(x["end"], float(x["val"]), x["filed"]) for x in node.get("units", {}).get("shares", [])
           if "filed" in x and "end" in x]
    out.sort(key=lambda r: r[2])
    return out


def pit(series, day: str):
    """Valores conocidos al día `day` (publicados antes): {año_fiscal: valor}, el más reciente gana."""
    out = {}
    for fy, v, filed in series:          # ordenada por fecha de publicación
        if filed >= day:
            break
        out[fy] = v
    return out


def load_fundamentals(zpath, ciks):
    """{cik: {"ni": [...], ..., "cover": [...]}} sólo para las empresas que hacen falta."""
    out = {}
    with zipfile.ZipFile(zpath) as z:
        for cik in ciks:
            try:
                with z.open(f"CIK{cik:010d}.json") as f:
                    facts = json.load(f).get("facts", {})
            except KeyError:
                continue
            d = {k: annual(facts, k) for k in CONCEPTS}
            d["cover"] = cover_shares(facts)
            if d["ni"] and d["eq"]:
                out[cik] = d
    return out


# ================================================================ precios

def bars(a: Alpaca, symbols, start, timeframe="1Day", adjustment="all", chunk=50):
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat(timespec="seconds")
    out = {}

    def fetch(batch):
        token = None
        while True:
            params = {"symbols": ",".join(batch), "timeframe": timeframe, "start": start.isoformat(), "end": end,
                      "limit": 10000, "adjustment": adjustment, "feed": "sip"}
            if token:
                params["page_token"] = token
            js = a._get(f"{DATA}/v2/stocks/bars", params)
            for s, bb in (js.get("bars") or {}).items():
                r = out.setdefault(s, {"t": [], "c": [], "v": []})
                for b in bb:
                    r["t"].append(b["t"][:10]); r["c"].append(b["c"]); r["v"].append(b["v"])
            token = js.get("next_page_token")
            if not token:
                break

    def safe(batch):
        try:
            fetch(batch)
        except Exception:
            if len(batch) == 1:
                return
            h = len(batch) // 2
            safe(batch[:h]); safe(batch[h:])

    for i in range(0, len(symbols), chunk):
        safe(symbols[i:i + chunk])
        if (i // chunk) % 20 == 0:
            log(f"  {timeframe}/{adjustment}: {min(i + chunk, len(symbols))}/{len(symbols)}")
    return out


def all_assets(a: Alpaca):
    out = {}
    for status in ("active", "inactive"):
        for x in a._get(f"{a.trading}/v2/assets", {"status": status, "asset_class": "us_equity"}):
            name = (x.get("name") or "").upper()
            if not TICKER.match(x["symbol"]) or any(w in name for w in ETF_WORDS):
                continue
            if status == "active" and x.get("exchange") not in C.EXCHANGES:
                continue
            if x["symbol"] not in out or status == "active":
                out[x["symbol"]] = {"name": x.get("name") or "", "status": status}
    return out


def matrix(raw: dict, dates: list[str], syms: list[str], field: str):
    pos = {d: i for i, d in enumerate(dates)}
    M = np.full((len(syms), len(dates)), np.nan)
    for k, s in enumerate(syms):
        r = raw.get(s)
        if not r:
            continue
        for t, x in zip(r["t"], r[field]):
            j = pos.get(t)
            if j is not None:
                M[k, j] = x
    return M


# ================================================================ reglas

def history(series, day, n=10):
    """Los últimos n años fiscales conocidos al día `day`, del más viejo al más nuevo."""
    d = pit(series, day)
    ks = sorted(d)[-n:]
    return ks, [d[k] for k in ks]


def evaluate(f: dict, day: str, roe_min=0.15):
    """Revisa las reglas 1-5. Devuelve (pasa, datos) con lo necesario para valuar y ordenar."""
    ni_k, ni = history(f["ni"], day)
    eq = pit(f["eq"], day)
    yrs = [(k, v) for k, v in zip(ni_k, ni) if eq.get(k, 0) > 0]
    if len(ni_k) < 5 or len(yrs) < 5:
        return False, None
    if ni_k[-1] < str(int(day[:4]) - 2):          # el último 10-K tiene que ser reciente
        return False, None
    roe = [v / eq[k] for k, v in yrs]
    if eq.get(ni_k[-1], 0) <= 0:
        return False, None
    if np.mean(np.array(roe) >= roe_min) < 0.8 or roe[-1] < roe_min:
        return False, None
    if min(ni[-5:]) <= 0:
        return False, None
    ltd = pit(f["ltd"], day)
    debt = ltd.get(ni_k[-1], ltd[max(ltd)] if ltd else 0.0)
    if debt > 3 * ni[-1]:
        return False, None
    ocf, cap = pit(f["ocf"], day), pit(f["capex"], day)
    oe = [ocf[k] - cap.get(k, 0.0) for k in ni_k if k in ocf]
    if len(oe) < 5 or np.mean(np.array(oe) > 0) < 0.8 or oe[-1] <= 0:
        return False, None
    sh_k, sh = history(f["sh"], day, 6)
    if len(sh) >= 5 and sh[0] > 0 and sh[-1] / sh[0] > 1.05:
        return False, None
    rev_k, rev = history(f["rev"], day, 6)
    g = 0.0
    if len(rev) >= 4 and rev[0] > 0 and rev[-1] > 0:
        g = (rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1
    return True, {"roe_med": float(np.median(roe)), "oe": float(np.mean(oe[-3:])), "g": float(np.clip(g, 0, 0.08)),
                  "debt": float(debt), "sh": sh[-1] if sh else None, "fy": ni_k[-1]}


def intrinsic(info, r=0.10, gt=0.03, years=10):
    """Flujo descontado: ganancias del dueño promedio de 3 años, crecen g (0 a 8%) 10 años, luego 3%, al 10%."""
    oe, g = info["oe"], info["g"]
    pv, cf = 0.0, oe
    for t in range(1, years + 1):
        cf *= 1 + g
        pv += cf / (1 + r) ** t
    pv += cf * (1 + gt) / (r - gt) / (1 + r) ** years
    return pv - info["debt"]


def shares_now(f, day):
    """Acciones de la portada más reciente publicada antes de `day` (y su fecha de publicación)."""
    best = None
    for end, v, filed in f["cover"]:
        if filed >= day:
            break
        best = (v, filed)
    return best


# ================================================================ simulación

def simulate(R, picks_by_reb, reb_idx, spy_row, n_slots, cost=COST):
    """Rendimiento diario. R = rendimientos diarios (sím × días, nan = sin precio -> 0%).
    picks_by_reb[j] = filas compradas al cierre de reb_idx[j]; lugares vacíos van a SPY."""
    T = R.shape[1]
    out = np.zeros(T)
    w = {}                                   # pesos actuales (fila -> peso)
    turnover = []
    for j, i0 in enumerate(reb_idx):
        i1 = reb_idx[j + 1] if j + 1 < len(reb_idx) else T - 1
        rows = list(picks_by_reb[j])
        tgt = {r: 1.0 / n_slots for r in rows}
        if len(rows) < n_slots:
            tgt[spy_row] = tgt.get(spy_row, 0) + (n_slots - len(rows)) / n_slots
        keys = set(tgt) | set(w)
        to = sum(abs(tgt.get(k, 0) - w.get(k, 0)) for k in keys)
        turnover.append(to)
        out[i0] -= cost * to                 # se cobra el día de la compra
        w = dict(tgt)
        for t in range(i0 + 1, i1 + 1):
            tot = 0.0
            nw = {}
            for k, wk in w.items():
                x = R[k, t]
                x = 0.0 if not np.isfinite(x) else x
                tot += wk * x
                nw[k] = wk * (1 + x)
            s = sum(nw.values()) or 1.0
            out[t] += tot
            w = {k: v / s for k, v in nw.items()}
    return out, turnover


def stats(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    sd = r.std()
    return {"cum": float(eq[-1] - 1), "ann": float(eq[-1] ** (252 / len(r)) - 1),
            "vol": float(sd * np.sqrt(252)), "sharpe": float(r.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0,
            "dd": dd}


def ols(r, m):
    X = np.column_stack([np.ones(len(m)), m])
    a, b = np.linalg.lstsq(X, r, rcond=None)[0]
    return a, b


def boot_alpha(r, m, rng):
    n = len(r)
    nb = int(np.ceil(n / BLOCK))
    al, ex = [], []
    for _ in range(BOOT):
        st = rng.integers(0, n, nb)
        ix = ((st[:, None] + np.arange(BLOCK)[None, :]) % n).ravel()[:n]
        a, _ = ols(r[ix], m[ix])
        al.append(a * 252)
        ex.append((r[ix].mean() - m[ix].mean()) * 252)
    q = lambda x: (float(np.percentile(x, 2.5)), float(np.percentile(x, 97.5)), float(np.mean(np.array(x) > 0)))
    return q(al), q(ex)


# ================================================================ principal

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=11)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    N = args.top
    rng = np.random.default_rng(7)

    # ---------- EDGAR
    log("bajando companyfacts.zip de EDGAR (≈1 GB)…")
    zpath = download(FACTS_URL, f"{CACHE}/companyfacts.zip")
    tick = requests.get(TICKERS_URL, headers=UA, timeout=60).json()
    cik_by_ticker = {v["ticker"].upper().replace("-", "."): int(v["cik_str"]) for v in tick.values()}
    names = index_facts(zpath)
    by_name = {}
    for cik, nm in names.items():
        by_name.setdefault(norm_name(nm), set()).add(cik)
    log(f"EDGAR: {len(names):,} empresas con XBRL, {len(cik_by_ticker):,} tickers actuales")

    # ---------- Alpaca: candidatas con barras mensuales
    a = Alpaca()
    now = dt.datetime.now(NY)
    today = now.date()
    cal = a.calendar(today - dt.timedelta(days=40), today + dt.timedelta(days=10))
    last = last_completed_session(cal, now)
    start = today - dt.timedelta(days=int(365 * args.years))
    assets = all_assets(a)
    syms = sorted(assets)
    log(f"Alpaca: {len(syms):,} acciones ({sum(1 for x in assets.values() if x['status'] == 'inactive'):,} deslistadas); "
        "barras mensuales para elegir candidatas…")
    mon = bars(a, syms, start, "1Month", "raw", chunk=200)
    months = sorted({t for r in mon.values() for t in r["t"]})
    M = matrix(mon, months, syms, "c") * matrix(mon, months, syms, "v")
    P = matrix(mon, months, syms, "c")
    M[~(P >= C.MIN_PRICE)] = np.nan
    top = int(UNIVERSE_N * 1.6)
    cand = set()
    for j in range(len(months)):
        col = np.where(np.isfinite(M[:, j]), M[:, j], -np.inf)
        best = np.argsort(-col)[:top]
        cand.update(int(k) for k in best if np.isfinite(col[k]))
    cand = sorted(syms[k] for k in cand)
    log(f"candidatas (alguna vez entre las {top} más negociadas del mes): {len(cand):,}")

    # ---------- símbolo -> CIK
    cik = {}
    how = {"ticker": 0, "nombre": 0}
    for s in cand:
        c = cik_by_ticker.get(s) if assets[s]["status"] == "active" else None
        if c and c in names:
            cik[s] = c; how["ticker"] += 1
            continue
        hits = by_name.get(norm_name(assets[s]["name"]), set())
        if len(hits) == 1:
            cik[s] = next(iter(hits)); how["nombre"] += 1
        elif not c and assets[s]["status"] == "inactive" and cik_by_ticker.get(s) in names:
            pass                                # el ticker hoy es de otra empresa: no se usa
    log(f"con CIK: {len(cik):,} de {len(cand):,} (por ticker {how['ticker']:,}, por nombre {how['nombre']:,})")
    fund = load_fundamentals(zpath, sorted(set(cik.values())))
    log(f"con fundamentales útiles (utilidad y capital en 10-K): {len(fund):,} empresas")

    # ---------- precios diarios
    universe_syms = sorted(set(cand) | set(BENCH))
    log("barras diarias con dividendos y sin ajustar…")
    adj = bars(a, universe_syms, start, "1Day", "all")
    raw = bars(a, universe_syms, start, "1Day", "raw")
    dates = [t for t in adj["SPY"]["t"] if t <= last]
    S = universe_syms
    row = {s: k for k, s in enumerate(S)}
    dates_np = np.array(dates)
    A = matrix(adj, dates, S, "c")
    Pr = matrix(raw, dates, S, "c")
    V = matrix(raw, dates, S, "v")
    with np.errstate(invalid="ignore", divide="ignore"):
        R = A[:, 1:] / A[:, :-1] - 1
    R = np.concatenate([np.full((len(S), 1), np.nan), R], axis=1)
    R[~np.isfinite(R)] = np.nan
    R[np.abs(R) > 1.5] = np.nan              # saltos imposibles (error de datos o cambio de dueño del ticker)
    log(f"panel diario: {len(S):,} símbolos × {len(dates)} sesiones ({dates[0]} a {dates[-1]})")

    # ---------- rebalanceos trimestrales
    reb = []
    for k, d in enumerate(dates):
        if k >= 64 and d[5:7] in ("01", "04", "07", "10") and (k == 0 or dates[k - 1][5:7] != d[5:7]):
            reb.append(k)
    log(f"{len(reb)} rebalanceos ({dates[reb[0]]} a {dates[reb[-1]]})")

    dv = np.where(Pr >= C.MIN_PRICE, Pr * V, np.nan)
    stock_rows = np.array([row[s] for s in S if s not in BENCH])
    VARS = [("A12", 0.12, None), ("A15", 0.15, None), ("A18", 0.18, None),
            ("B15", 0.15, 0.15), ("B25", 0.15, 0.25), ("B35", 0.15, 0.35)]
    picks = {v[0]: [] for v in VARS}
    picks["EW"] = []
    cover = []
    for i in reb:
        day = dates[i]
        sig = i - 1                          # señales con el cierre anterior
        win = dv[stock_rows, max(0, sig - 62):sig + 1]
        cnt = np.isfinite(win).sum(axis=1)
        with np.errstate(invalid="ignore"):
            avg = np.where(cnt >= 40, np.nanmean(win, axis=1), np.nan)
        avg = np.where(np.isfinite(Pr[stock_rows, sig]), avg, np.nan)
        order = np.argsort(-np.where(np.isfinite(avg), avg, -np.inf))
        uni = [int(stock_rows[k]) for k in order[:UNIVERSE_N] if np.isfinite(avg[k])]
        picks["EW"].append(uni)
        has = [r for r in uni if cik.get(S[r]) in fund]
        dead = [r for r in uni if assets.get(S[r], {}).get("status") == "inactive"]
        cover.append((day, len(uni), len(has), len(dead), sum(1 for r in dead if cik.get(S[r]) in fund)))
        for name, roe_min, mos in VARS:
            ok = []
            for r in has:
                f = fund[cik[S[r]]]
                p, info = evaluate(f, day, roe_min)
                if not p:
                    continue
                if mos is None:
                    ok.append((-info["roe_med"], r))
                    continue
                sn = shares_now(f, day)
                if not sn or info["oe"] <= 0:
                    continue
                shares, filed = sn
                # acciones de la portada llevadas al día de hoy con los splits (razón ajustado/sin ajustar)
                jf = max(0, min(sig, int(np.searchsorted(dates_np, filed))))
                fa, fr, sa, sr = A[r, jf], Pr[r, jf], A[r, sig], Pr[r, sig]
                if not all(np.isfinite([fa, fr, sa, sr])) or min(fa, fr, sa, sr) <= 0:
                    continue
                split = (sa / sr) / (fa / fr)
                mcap = shares / split * sr
                iv = intrinsic(info)
                if iv <= 0 or mcap > (1 - mos) * iv:
                    continue
                ok.append((mcap / iv, r))
            ok.sort()
            picks[name].append([r for _, r in ok[:N]])
        if len(picks["A15"]) % 8 == 1:
            log(f"  {day}: universo {len(uni)}, con datos {len(has)}, A15 {len(picks['A15'][-1])}, "
                f"B25 {len(picks['B25'][-1])}: {' '.join(S[r] for r in picks['A15'][-1][:15])}")

    # ---------- resultados
    spy_row = row["SPY"]
    t0 = reb[0]
    series = {}
    for name in [v[0] for v in VARS]:
        r, to = simulate(R, picks[name], reb, spy_row, N)
        series[name] = (r[t0 + 1:], to, np.mean([len(x) for x in picks[name]]))
    r, to = simulate(R, picks["EW"], reb, spy_row, UNIVERSE_N)
    series["EW"] = (r[t0 + 1:], to, np.mean([len(x) for x in picks["EW"]]))
    for b in BENCH:
        x = np.nan_to_num(R[row[b], t0 + 1:])
        series[b] = (x, [0.0], 1)
    spy = series["SPY"][0]
    D = dates[t0 + 1:]
    first = {b: next((dates[k] for k in range(len(dates)) if np.isfinite(A[row[b], k])), "—") for b in BENCH}

    label = {"A12": "A. Calidad Buffett, ROE 12%", "A15": "A. Calidad Buffett, ROE 15%",
             "A18": "A. Calidad Buffett, ROE 18%", "B15": "B. Calidad + margen de seguridad 15%",
             "B25": "B. Calidad + margen de seguridad 25%", "B35": "B. Calidad + margen de seguridad 35%",
             "EW": f"Universo ({UNIVERSE_N} más negociadas) pesos iguales", "SPY": "SPY", "QUAL": "QUAL (iShares calidad)",
             "MOAT": "MOAT (VanEck foso)"}

    print(f"\n# Backtest Buffett: {D[0]} a {D[-1]} ({len(D)} sesiones, {len(reb)} rebalanceos trimestrales)\n")
    print(f"{N} empresas por canasta, mismo peso, costo {COST:.2%} por peso que cambia, lugares vacíos en SPY. "
          f"Universo de cada fecha: {UNIVERSE_N} acciones más negociadas (incluye deslistadas).\n")
    print("| Versión | Acumulado | Anual | Volatilidad | Sharpe | Peor caída | Beta | Alfa anual (IC 95%) | Prob. alfa > 0 | Ventaja anual vs SPY (IC 95%) | Empresas promedio | Rotación anual |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, (r, to, n) in series.items():
        s = stats(r)
        a_, b_ = ols(r, spy)
        if k == "SPY":
            print(f"| {label[k]} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['vol']:.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | 1.00 | — | — | — | — | — |")
            continue
        (al, ah, ap), (el, eh, _) = boot_alpha(r, spy, rng)
        note = f" (desde {first[k]})" if k in ("QUAL", "MOAT") and first[k] > D[0] else ""
        print(f"| {label[k]}{note} | {s['cum']:+.1%} | {s['ann']:+.1%} | {s['vol']:.1%} | {s['sharpe']:.2f} | {s['dd']:.1%} | "
              f"{b_:.2f} | {a_ * 252:+.1%} ({al:+.1%} a {ah:+.1%}) | {ap:.0%} | {(r.mean() - spy.mean()) * 252:+.1%} ({el:+.1%} a {eh:+.1%}) | "
              f"{n:.0f} | {np.sum(to) / (len(D) / 252):.0%} |")

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

    print("\n## Cobertura de datos (universo de cada fecha)\n")
    print("| Fecha | Universo | Con fundamentales | Deslistadas después | Deslistadas con fundamentales |")
    print("|---|---|---|---|---|")
    for c in cover[::4] + ([cover[-1]] if (len(cover) - 1) % 4 else []):
        print(f"| {c[0]} | {c[1]} | {c[2]} ({c[2] / max(c[1], 1):.0%}) | {c[3]} | {c[4]} |")

    print("\n## Canastas (primeras y última)\n")
    for j in (0, len(reb) // 2, len(reb) - 1):
        print(f"- {dates[reb[j]]} A15: {' '.join(S[r] for r in picks['A15'][j])}")
        print(f"- {dates[reb[j]]} B25: {' '.join(S[r] for r in picks['B25'][j]) or '(ninguna; todo en SPY)'}")
    print("\nNotas: ganancias del dueño = flujo operativo menos todo el capex (más conservador que Buffett, que resta sólo "
          "el capex de mantenimiento). Bancos y aseguradoras casi nunca pasan la regla de deuda. Si una acción deja de "
          "cotizar mientras se tiene, ese peso queda congelado (0%) hasta el siguiente rebalanceo. Alpaca no tiene "
          "precios antes de 2016. Las empresas deslistadas se ligan a EDGAR por nombre; las que no se encuentran quedan "
          "fuera de las canastas pero sí en el universo con pesos iguales. Nada de esto es asesoría financiera.")


if __name__ == "__main__":
    main()
