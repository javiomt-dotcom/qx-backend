"""Crecimiento y agresivo contra el S&P 500: ¿se puede quedar arriba sin más riesgo? (investigación)

Mezclas con rebalanceo mensual, precios ajustados de Yahoo, tres periodos: 2016-01, 2004-01 y 1999-04 (inicio de QQQ) a hoy.
Uso: python research_profiles.py
"""
import numpy as np

from research_portfolios import yahoo_monthly, stats, pct, num

MIXES = {
    "S&P 500 (SPY)": {"SPY": 1},
    "Crecimiento · Pegado al S&P (80 SPY/15 BND/5 GLD)": {"SPY": .80, "AGG": .15, "GLD": .05},
    "Crecimiento · Sencillo (60 VTI/20 VXUS/15 BND/5 GLD)": {"VTI": .60, "VXUS": .20, "AGG": .15, "GLD": .05},
    "Agresivo · Pegado al S&P (95 SPY/5 BND)": {"SPY": .95, "AGG": .05},
    "Agresivo · Sencillo (71 VTI/24 VXUS/5 BND)": {"VTI": .7125, "VXUS": .2375, "AGG": .05},
    "100% S&P 500 sin bonos": {"SPY": 1},
    "70% S&P + 30% Nasdaq 100 (QQQ)": {"SPY": .7, "QQQ": .3},
    "100% Nasdaq 100 (QQQ)": {"QQQ": 1},
    "70% S&P + 30% momentum (SPMO)": {"SPY": .7, "SPMO": .3},
    "S&P con 25% de deuda (1.25x)": {"SPY": 1.25, "_borrow": -.25},
    # Candidatas fijadas ANTES de ver resultados (reglas simples, sin optimizar pesos):
    "Crec. A · 80% (70 SPY/30 QQQ) + 15 BND + 5 GLD": {"SPY": .56, "QQQ": .24, "AGG": .15, "GLD": .05},
    "Crec. B · 100% (70 SPY/30 QQQ), sin bonos": {"SPY": .7, "QQQ": .3},
    "Crec. C · 85% (50 SPY/50 QQQ) + 15 BND": {"SPY": .425, "QQQ": .425, "AGG": .15},
    "Agr. A · 100% (50 SPY/50 QQQ)": {"SPY": .5, "QQQ": .5},
    "Agr. B · 100% (70 SPY/30 SPMO)": {"SPY": .7, "SPMO": .3},
    "Agr. C · 115% S&P (15% deuda)": {"SPY": 1.15, "_borrow": -.15},
}


def main():
    syms = sorted({s for w in MIXES.values() for s in w if not s.startswith("_")} | {"BIL"})
    px = {s: yahoo_monthly(s) for s in syms}
    spy = px["SPY"]
    for start in (201601, 200401, 199904):  # 1999-04: inicio de QQQ, incluye la caída de 2000-2002
        print(f"\n### Desde {start // 100}-{start % 100:02d}\n")
        print("| Mezcla | Rend. anual | Diferencia vs S&P | Peor caída | Volatilidad | Beta | Alfa anual (t) | Desde |")
        print("|---|---|---|---|---|---|---|---|")
        for name, w in MIXES.items():
            real = [s for s in w if not s.startswith("_")]
            ms = sorted(m for m in spy if m >= start and all(m in px[s] for s in real))
            if len(ms) < 36:
                print(f"| {name} | sin datos | | | | | | |")
                continue
            rf = np.array([px["BIL"].get(m, 0.002) for m in ms])
            r = np.array([sum(px[s][m] * x for s, x in w.items() if s in px) + w.get("_borrow", 0) * (rf[i] + 0.005 / 12)
                          for i, m in enumerate(ms)])
            b = np.array([spy[m] for m in ms])
            st, sb = stats(r, rf, b), stats(b, rf, b)
            print(f"| {name} | {pct(st['cagr'])} | {pct(st['cagr'] - sb['cagr'])} | {pct(st['dd'], 0)} | {pct(st['vol'], 0)} | "
                  f"{num(st['beta'])} | {pct(st['alpha'])} ({num(st['t'], 1)}) | {ms[0] // 100} |")


if __name__ == "__main__":
    main()
