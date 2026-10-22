"""Motor cuantitativo: port a numpy del motor de terminal.html.

Mismo modelo: 12 rasgos de la víspera, z-score por día entre acciones (recortado a ±3),
regresión logística que predice "estar entre los TOPN que más suben la siguiente sesión",
walk-forward de 60 sesiones re-entrenando cada 20, decaimiento temporal (vida media 120),
picks reales del registro con peso ×5 y recalibración tipo Platt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from . import config as C

FEATS = [
    ("r1", "Retorno del día"), ("rvol", "Volumen relativo"), ("clv", "Cierre en el rango"),
    ("rng", "Expansión de rango"), ("r5", "Momentum 5 días"), ("dh20", "Distancia a máx. 20d"),
    ("rsi", "RSI 14"), ("vol20", "Volatilidad 20d"), ("gap", "Gap de apertura"),
    ("s20", "vs media 20d"), ("s50", "vs media 50d"), ("mrel", "vs mercado (SPY)"),
]
K = len(FEATS)


@dataclass
class Panel:
    """Precios alineados a las fechas del SPY: arrays (n_símbolos, n_fechas), NaN donde falta."""
    dates: list[str]
    syms: list[str]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    @property
    def idx(self):
        return {s: k for k, s in enumerate(self.syms)}

    @classmethod
    def from_bars(cls, bars: dict[str, dict[str, list]], ref: str = "SPY") -> "Panel":
        """bars[s] = {"t": [fechas ISO], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}"""
        if ref not in bars:
            raise ValueError("Falta el historial del SPY, que define el calendario")
        dates = list(bars[ref]["t"])
        pos = {d: i for i, d in enumerate(dates)}
        syms = list(bars)
        arr = {k: np.full((len(syms), len(dates)), np.nan) for k in "ohlcv"}
        for si, s in enumerate(syms):
            r = bars[s]
            ii = [pos.get(d, -1) for d in r["t"]]
            for k in "ohlcv":
                vals = np.asarray(r[k], dtype=float)
                ok = np.array([i >= 0 for i in ii])
                if ok.any():
                    arr[k][si, np.array(ii)[ok]] = vals[ok]
        return cls(dates, syms, arr["o"], arr["h"], arr["l"], arr["c"], arr["v"])


def _sma(c, i, n):
    return c[:, i - n + 1:i + 1].sum(axis=1) / n          # NaN si falta un día, como en la terminal


def atr(p: Panel, i: int, n: int = 14) -> np.ndarray:
    h, l, c = p.h[:, i - n + 1:i + 1], p.l[:, i - n + 1:i + 1], p.c[:, i - n:i]
    tr = np.fmax(h - l, np.fmax(np.abs(h - c), np.abs(l - c)))
    with np.errstate(invalid="ignore"):
        return np.nanmean(np.where(np.isfinite(tr), tr, np.nan), axis=1)


def raw_features(p: Panel, i: int, spy_row: int | None) -> np.ndarray:
    """Matriz (n_símbolos, K) de rasgos del día i; filas con NaN no son válidas."""
    c, o, h, l, v = p.c, p.o, p.h, p.l, p.v
    n = len(p.syms)
    F = np.full((n, K), np.nan)
    if i < 55:
        return F
    with np.errstate(invalid="ignore", divide="ignore"):
        vwin = v[:, i - 20:i]
        vavg = np.nanmean(vwin, axis=1)
        arw = (h[:, i - 20:i] - l[:, i - 20:i]) / c[:, i - 21:i - 1]
        ar = np.nanmean(arw, axis=1)
        mx = np.nanmax(h[:, i - 19:i + 1], axis=1)
        rs = c[:, i - 19:i + 1] / c[:, i - 20:i] - 1
        sd = np.nanstd(rs, axis=1)
        d = c[:, i - 13:i + 1] - c[:, i - 14:i]
        g = np.nansum(np.where(d > 0, d, 0), axis=1)
        lo = np.nansum(np.where(d < 0, -d, 0), axis=1)
        r1 = c[:, i] / c[:, i - 1] - 1
        hl = h[:, i] - l[:, i]
        spyr = 0.0
        if spy_row is not None and np.isfinite(c[spy_row, i]) and np.isfinite(c[spy_row, i - 1]):
            spyr = c[spy_row, i] / c[spy_row, i - 1] - 1
        vi = np.where(np.isfinite(v[:, i]) & (v[:, i] > 0), v[:, i], 1.0)
        F[:, 0] = r1
        F[:, 1] = np.log(vi / np.where(np.isfinite(vavg) & (vavg > 0), vavg, 1.0))
        F[:, 2] = np.where(hl > 0, (c[:, i] - l[:, i]) / hl, 0.5)
        F[:, 3] = np.where(np.isfinite(ar) & (ar != 0), (hl / c[:, i - 1]) / ar, 1.0)
        F[:, 4] = c[:, i] / c[:, i - 5] - 1
        F[:, 5] = c[:, i] / mx - 1
        F[:, 6] = np.where(lo == 0, 100.0, 100 - 100 / (1 + g / np.where(lo == 0, 1, lo)))
        F[:, 7] = sd
        F[:, 8] = o[:, i] / c[:, i - 1] - 1
        F[:, 9] = c[:, i] / _sma(c, i, 20) - 1
        F[:, 10] = c[:, i] / _sma(c, i, 50) - 1
        F[:, 11] = r1 - spyr
    base_ok = np.isfinite(c[:, i]) & np.isfinite(c[:, i - 1]) & np.isfinite(c[:, i - 50])
    F[~base_ok] = np.nan
    return F


def zday(F: np.ndarray) -> np.ndarray:
    m = F.mean(axis=0)
    sd = F.std(axis=0)
    sd[sd == 0] = 1
    return np.clip((F - m) / sd, -3, 3)


def regime_at(p: Panel, spy_row, i, breadth):
    if spy_row is None or i < 50 or not np.isfinite(p.c[spy_row, i]):
        return {"key": "neu", "breadth": breadth}
    c = p.c[spy_row]
    m20, m50 = c[i - 19:i + 1].mean(), c[i - 49:i + 1].mean()
    above50, trend = c[i] > m50, m20 > m50
    key = "neu"
    if above50 and trend and breadth >= 0.5:
        key = "fav"
    elif not above50 and (breadth < 0.4 or not trend):
        key = "weak"
    return {"key": key, "above50": bool(above50), "trend": bool(trend), "breadth": float(breadth),
            "dist50": float(c[i] / m50 - 1)}


def trade_at(o1, h1, l1, c1, c0, a):
    """Compra en la apertura; salta si abre > GAP_ATR·ATR arriba del cierre; stop/objetivo desde la entrada.
    Devuelve (ret, flag). Si el día toca stop y objetivo, cuenta el stop."""
    if not all(np.isfinite(x) for x in (o1, h1, l1, c1, c0, a)) or o1 <= 0:
        return float("nan"), "NODATA"
    if o1 - c0 > C.GAP_ATR * a:
        return float("nan"), "SKIPPED"
    stop, tgt = o1 - C.STOP_ATR * a, o1 + C.TGT_ATR * a
    if l1 <= stop:
        return stop / o1 - 1, "STOP"
    if h1 >= tgt:
        return tgt / o1 - 1, "TARGET"
    return c1 / o1 - 1, "OK"


@dataclass
class Day:
    i: int
    date: str
    rows: np.ndarray            # índices de símbolos válidos ese día
    Z: np.ndarray               # (len(rows), K)
    raw: np.ndarray
    regime: dict
    rets: np.ndarray | None = None
    top: np.ndarray | None = None   # booleano por fila
    trd: list | None = None
    uni_oc: float = float("nan")


def topn_for(n):
    return 20 if n >= 200 else max(3, round(n / 10))


def build_dataset(p: Panel, model_rows: np.ndarray, topn: int) -> list[Day]:
    spy = p.idx.get("SPY")
    days = []
    min_rows = min(40, max(10, int(len(model_rows) * 0.6)))
    for i in range(55, len(p.dates)):
        F = raw_features(p, i, spy)[model_rows]
        ok = np.isfinite(F).all(axis=1)
        if ok.sum() < min_rows:
            continue
        rows = model_rows[ok]
        Fv = F[ok]
        Z = zday(Fv)
        breadth = float((Fv[:, 10] > 0).mean())
        d = Day(i, p.dates[i], rows, Z, Fv, regime_at(p, spy, i, breadth))
        if i + 1 < len(p.dates):
            with np.errstate(invalid="ignore", divide="ignore"):
                rets = p.c[rows, i + 1] / p.c[rows, i] - 1
                oc = p.c[rows, i + 1] / p.o[rows, i + 1] - 1
            fin = np.isfinite(rets)
            order = np.argsort(-np.where(fin, rets, -np.inf))
            top = np.zeros(len(rows), bool)
            top[order[:topn]] = True
            top &= fin
            a = atr(p, i)[rows]
            d.rets, d.top = rets, top
            d.trd = [trade_at(p.o[r, i + 1], p.h[r, i + 1], p.l[r, i + 1], p.c[r, i + 1], p.c[r, i], a[k])
                     for k, r in enumerate(rows)]
            d.uni_oc = float(np.nanmean(oc)) if np.isfinite(oc).any() else float("nan")
        days.append(d)
    return days


def _flatten(days: list[Day], extra):
    lab = [d for d in days if d.rets is not None]
    L = len(lab)
    Xs, ys, ws = [], [], []
    for di, d in enumerate(lab):
        fin = np.isfinite(d.rets)
        Xs.append(d.Z[fin]); ys.append(d.top[fin].astype(float))
        ws.append(np.full(fin.sum(), 0.5 ** ((L - 1 - di) / C.HALF_LIFE)))
    for e in extra or []:
        Xs.append(e["z"][None, :]); ys.append(np.array([float(e["y"])])); ws.append(np.array([float(C.REAL_W)]))
    if not Xs:
        return np.zeros((0, K)), np.zeros(0), np.zeros(0)
    return np.vstack(Xs), np.concatenate(ys), np.concatenate(ws)


def fit_logit(days, w0, iters, extra=None):
    X, y, W = _flatten(days, extra)
    w = np.zeros(K + 1) if w0 is None else w0.copy()
    if w0 is None:
        pr = max(1e-3, y.mean() if len(y) else 1e-3)
        w[K] = np.log(pr / (1 - pr))
    if not len(y):
        return w
    sw = W.sum()
    for _ in range(iters):
        s = X @ w[:K] + w[K]
        e = (1 / (1 + np.exp(-s)) - y) * W
        g = X.T @ e
        w[:K] -= 0.8 * (g / sw + 2e-3 * w[:K])
        w[K] -= 0.8 * e.sum() / sw
    return w


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_calib(pairs):
    a, b = 0.0, 1.0
    if not pairs:
        return {"a": a, "b": b}
    P = np.array([x["p"] for x in pairs]); Y = np.array([x["y"] for x in pairs], float)
    Wt = np.array([x["w"] for x in pairs], float); sw = Wt.sum()
    l = _logit(P)
    for _ in range(400):
        e = (1 / (1 + np.exp(-(a + b * l))) - Y) * Wt
        a -= 0.5 * e.sum() / sw
        b -= 0.05 * (e * l).sum() / sw
        b = min(3, max(0.2, b))
    return {"a": float(a), "b": float(b)}


def apply_cal(cal, p):
    return 1 / (1 + np.exp(-(cal["a"] + cal["b"] * _logit(p))))


def score(w, Z):
    return 1 / (1 + np.exp(-(Z @ w[:K] + w[K])))


def real_samples(days, p: Panel, logged):
    """logged: [{"date": "YYYY-MM-DD", "picks": [{"s": sym, "p": p_raw}]}]"""
    by_date = {d.date: d for d in days if d.rets is not None}
    idx = p.idx
    out = []
    for f in logged or []:
        d = by_date.get(f["date"])
        if not d:
            continue
        pos = {r: k for k, r in enumerate(d.rows)}
        for pk in f["picks"]:
            k = pos.get(idx.get(pk["s"], -1))
            if k is None or not np.isfinite(d.rets[k]):
                continue
            out.append({"date": f["date"], "s": pk["s"], "z": d.Z[k], "y": int(d.top[k]), "p": pk["p"], "ret": float(d.rets[k])})
    return out


def reasons(w, z):
    contrib = w[:K] * z
    top = np.argsort(-np.abs(contrib))[:2]
    return [{"feat": FEATS[j][1], "dir": "up" if z[j] > 0 else "down", "pos": bool(contrib[j] > 0)} for j in top]


@dataclass
class Result:
    syms: list
    topn: int
    last: Day
    forecast: list = field(default_factory=list)
    w: np.ndarray | None = None
    cal: dict | None = None
    bt: list = field(default_factory=list)
    n_real: int = 0


def run(p: Panel, model_syms: list[str], logged=None) -> Result:
    idx = p.idx
    rows = np.array(sorted(idx[s] for s in model_syms if s in idx))
    topn = topn_for(len(rows))
    days = build_dataset(p, rows, topn)
    labeled = [d for d in days if d.rets is not None]
    if len(labeled) < 30:
        raise RuntimeError(f"Muy pocas sesiones con datos suficientes ({len(labeled)})")
    real = real_samples(days, p, logged)
    T = min(C.TEST_DAYS, len(labeled) // 3)
    start = len(labeled) - T
    bt, cal_pairs, w = [], [], None
    for t in range(start, len(labeled)):
        if (t - start) % 20 == 0:
            w = fit_logit(labeled[:t], w, 60 if w is not None else 220, [x for x in real if x["date"] < labeled[t].date])
        d = labeled[t]
        pr = score(w, d.Z)
        rk = np.argsort(-pr)[:C.PICKS]
        tr = [d.trd[k] for k in rk]
        skipped = sum(1 for r, f in tr if f == "SKIPPED")
        pk = sum(r for r, f in tr if np.isfinite(r)) / len(rk)
        for k in rk:
            cal_pairs.append({"p": float(pr[k]), "y": int(d.top[k]), "w": 1})
        bt.append({"date": d.date, "picks": pk, "uni": d.uni_oc, "hits": int(d.top[rk].sum()),
                   "skipped": skipped, "stops": sum(1 for _, f in tr if f == "STOP"), "regime": d.regime["key"]})
    w_all = fit_logit(labeled, w, 80, real)
    cal = fit_calib(cal_pairs + [{"p": x["p"], "y": x["y"], "w": C.REAL_W} for x in real])
    last = days[-1]
    pr = score(w_all, last.Z)
    pc = apply_cal(cal, pr)
    order = np.argsort(-pr)
    forecast = [{"s": p.syms[last.rows[k]], "row": int(last.rows[k]), "p_raw": float(pr[k]), "p_cal": float(pc[k]),
                 "z": last.Z[k], "r1": float(last.raw[k, 0]), "reasons": reasons(w_all, last.Z[k])} for k in order]
    return Result(syms=[p.syms[r] for r in rows], topn=topn, last=last, forecast=forecast, w=w_all, cal=cal, bt=bt, n_real=len(real))


def select_picks(res: Result, p: Panel, sp500: frozenset, n=C.PICKS, max_non_sp=C.MAX_NON_SP, max_jump=C.MAX_JUMP):
    """Aplica las reglas de la lista: sin saltos > max_jump y máximo max_non_sp fuera del S&P."""
    out, non_sp = [], 0
    for x in res.forecast:
        if x["r1"] > max_jump:
            continue
        is_sp = x["s"] in sp500
        if not is_sp and non_sp >= max_non_sp:
            continue
        out.append(x)
        non_sp += (not is_sp)
        if len(out) == n:
            break
    return out


def plan(p: Panel, i: int, pick: dict, regime_key: str):
    r = pick["row"]
    c = float(p.c[r, i]); a = float(atr(p, i)[r])
    stop, tgt = c - C.STOP_ATR * a, c + C.TGT_ATR * a
    mult = C.REGIME_MULT[regime_key]
    shares = int(max(0, min(C.CAPITAL * C.RISK * mult / (c - stop), C.CAPITAL * C.MAX_POS / c))) if c > stop else 0
    return {"close": c, "atr": a, "max_entry": c + C.GAP_ATR * a, "stop": stop, "target": tgt, "shares": shares}
