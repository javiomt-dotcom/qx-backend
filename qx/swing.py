"""Estrategia "swing" de 5 días: la ganadora de research.py (versión L).

Cada día se compran 10 acciones en la apertura con 1/5 del capital y se venden al cierre de la
quinta sesión. Puntaje = modelo de retorno a 5 días (ridge, ajustado por volatilidad, con
decaimiento temporal) normalizado + tendencia (z de la distancia a la media de 50 días).
Filtros: ATR ≤ 8% del precio, sin saltos > 40% el día de la señal, máximo 3 fuera del S&P.
"""
from __future__ import annotations

import numpy as np

from . import config as C, engine as E

HOLD = 5
PICKS = 10
MAX_ATRP = 0.08
COST = 0.001
TRAIN_DAYS = 750


def _day(p, i, rows_all, is_sp, spy):
    F = E.raw_features(p, i, spy)[rows_all]
    with np.errstate(invalid="ignore", divide="ignore"):
        dv = np.nanmean(p.c[rows_all, i - 19:i + 1] * p.v[rows_all, i - 19:i + 1], axis=1)
        liquid = is_sp | ((p.c[rows_all, i] >= C.MIN_PRICE) & (dv >= C.MIN_DOLLAR_VOL))
    ok = np.isfinite(F).all(axis=1) & liquid
    rows = rows_all[ok]
    a = E.atr(p, i)[rows]
    return rows, E.zday(F[ok]), F[ok], a / p.c[rows, i], is_sp[ok]


def fit_ridge(X, y, W, lam=50.0):
    Xb = np.hstack([X, np.ones((len(X), 1))])
    A = (Xb * W[:, None]).T @ Xb
    A[:-1, :-1] += lam * np.eye(X.shape[1]) * (W.sum() / len(W))
    return np.linalg.solve(A, (Xb * W[:, None]).T @ y)


def run(p: E.Panel, universe: list[str], sp500: frozenset, exclude=frozenset()):
    """Devuelve (picks del último día, info). Cada pick: s, row, score, model, trend, atrp, sp.
    exclude: símbolos que no se pueden comprar hoy (p. ej. reportan resultados durante los 5 días)."""
    idx = p.idx
    spy = idx["SPY"]
    rows_all = np.array(sorted(idx[s] for s in universe if s in idx))
    is_sp = np.array([p.syms[r] in sp500 for r in rows_all])
    n = len(p.dates)
    last = n - 1
    Xs, ys, ds = [], [], []
    for i in range(max(55, last - TRAIN_DAYS - HOLD), last - HOLD + 1):
        rows, Z, _, atrp, _ = _day(p, i, rows_all, is_sp, spy)
        if len(rows) < 50:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            g = p.c[rows, i + HOLD] / p.o[rows, i + 1] - 1          # apertura del día 1 al cierre del día 5
        ok = np.isfinite(g) & np.isfinite(atrp)
        Xs.append(Z[ok]); ys.append(np.clip(g[ok] / np.maximum(atrp[ok] * np.sqrt(HOLD), 1e-3), -3, 3))
        ds.append(np.full(ok.sum(), i))
    X, y, d = np.vstack(Xs), np.concatenate(ys), np.concatenate(ds)
    W = 0.5 ** ((last - HOLD - d) / C.HALF_LIFE)
    b = fit_ridge(X, y, W)
    rows, Z, raw, atrp, sp = _day(p, last, rows_all, is_sp, spy)
    m = Z @ b[:-1] + b[-1]
    mz = (m - m.mean()) / (m.std() or 1)
    score = mz + Z[:, 10]
    w_eff = np.append(b[:-1] / (m.std() or 1), 0.0)       # contribución de cada rasgo al puntaje, para el "por qué"
    w_eff[10] += 1.0
    out, non_sp, skipped = [], 0, []
    for k in np.argsort(-score):
        if raw[k, 0] > C.MAX_JUMP or not (atrp[k] <= MAX_ATRP):
            continue
        if p.syms[rows[k]] in exclude:
            skipped.append(p.syms[rows[k]])          # habría entrado a la lista
            continue
        if not sp[k] and non_sp >= C.MAX_NON_SP:
            continue
        out.append({"s": p.syms[rows[k]], "row": int(rows[k]), "score": float(score[k]), "model": float(mz[k]),
                    "trend": float(Z[k, 10]), "atrp": float(atrp[k]), "sp": bool(sp[k]),
                    "reasons": E.reasons(w_eff, Z[k])})
        non_sp += (not sp[k])
        if len(out) == PICKS:
            break
    return out, {"weights": {E.FEATS[j][0]: float(b[j]) for j in range(E.K)}, "n_train": int(len(y)), "universe": int(len(rows)),
                 "skipped": skipped}


def plan(p: E.Panel, i: int, pick: dict):
    c = float(p.c[pick["row"], i])
    a = float(E.atr(p, i)[pick["row"]])
    per = C.CAPITAL / HOLD / PICKS
    return {"close": c, "atr": a, "max_entry": c + C.GAP_ATR * a, "shares": int(per // c) if c > 0 else 0}


def score_session(p: E.Panel, session: str, cohorts: dict[str, list[str]], sessions: list[str]):
    """Retorno real del portafolio swing en `session`.

    cohorts: {signal_date: [símbolos]} de las listas guardadas. La lista con señal en la sesión k
    compra en la apertura de k+1 y vende al cierre de k+5; cada cohorte pesa 1/HOLD."""
    if session not in p.dates or session not in sessions:
        return None
    j = p.dates.index(session)
    pos = sessions.index(session)
    idx = p.idx
    parts, active, sells = [], 0, []
    for step in range(1, HOLD + 1):
        if pos - step < 0:
            continue
        sig = sessions[pos - step]
        syms = cohorts.get(sig)
        if not syms:
            continue
        active += 1
        rs = []
        for s in syms:
            r = idx.get(s)
            if r is None:
                continue
            with np.errstate(invalid="ignore", divide="ignore"):
                x = p.c[r, j] / p.o[r, j] - 1 - COST if step == 1 else p.c[r, j] / p.c[r, j - 1] - 1
            if np.isfinite(x):
                rs.append(float(x))
        parts.append(np.mean(rs) if rs else 0.0)
        if step == HOLD:
            sells.append(sig)
    if not active:
        return None
    spy = idx.get("SPY")
    spy_cc = float(p.c[spy, j] / p.c[spy, j - 1] - 1) if spy is not None else None
    return {"session": session, "ret": float(np.sum(parts)) / HOLD, "cohorts": active, "spy_cc": spy_cc}
