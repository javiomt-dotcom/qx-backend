"""Pruebas sin red de research_buffett.py: datos de su momento, reglas, valuación y simulación."""
import json
import os
import sys
import tempfile
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import research_buffett as B  # noqa: E402


def fy(year, val, filed, start=True):
    x = {"end": f"{year}-12-31", "val": val, "filed": filed, "form": "10-K"}
    if start:
        x["start"] = f"{year}-01-01"
    return x


def company(roe=0.25, debt=100.0, restated=None):
    ni, eq, ocf, cap, rev, sh = [], [], [], [], [], []
    for y in range(2010, 2020):
        filed = f"{y + 1}-02-20"
        ni.append(fy(y, 100.0 * 1.05 ** (y - 2010), filed))
        eq.append(fy(y, 100.0 * 1.05 ** (y - 2010) / roe, filed, start=False))
        ocf.append(fy(y, 120.0, filed)); cap.append(fy(y, 20.0, filed))
        rev.append(fy(y, 1000.0 * 1.04 ** (y - 2010), filed)); sh.append(fy(y, 50.0, filed))
    if restated:   # el 10-K de 2019 re-expresa la utilidad de 2018, publicado después
        ni.append(fy(2018, restated, "2020-02-20"))
    return {"facts": {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": ni}},
            "StockholdersEquity": {"units": {"USD": eq}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": cap}},
            "Revenues": {"units": {"USD": rev}},
            "LongTermDebtNoncurrent": {"units": {"USD": [fy(2019, debt, "2020-02-20", start=False)]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": sh}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2020-01-31", "val": 50.0, "filed": "2020-02-20", "form": "10-K"}]}}},
    }, "entityName": "Acme Widgets Inc"}


def test_pit_and_restatement():
    f = {k: B.annual(company(restated=-5.0)["facts"], k) for k in B.CONCEPTS}
    before = B.pit(f["ni"], "2020-01-15")
    after = B.pit(f["ni"], "2020-03-01")
    assert "2019" not in before and "2019" in after, "no debe ver el 10-K antes de publicarse"
    assert before["2018"] > 0 and after["2018"] == -5.0, "la re-expresión sólo cuenta después de publicarse"


def test_rules():
    good = {k: B.annual(company()["facts"], k) for k in B.CONCEPTS}
    good["cover"] = B.cover_shares(company()["facts"])
    ok, info = B.evaluate(good, "2020-04-01")
    assert ok and abs(info["roe_med"] - 0.25) < 1e-9 and info["oe"] == 100.0
    low = {k: B.annual(company(roe=0.10)["facts"], k) for k in B.CONCEPTS}
    assert not B.evaluate(low, "2020-04-01")[0], "ROE bajo no pasa"
    lev = {k: B.annual(company(debt=10_000.0)["facts"], k) for k in B.CONCEPTS}
    assert not B.evaluate(lev, "2020-04-01")[0], "mucha deuda no pasa"
    loss = {k: B.annual(company(restated=-5.0)["facts"], k) for k in B.CONCEPTS}
    assert B.evaluate(loss, "2020-01-15")[0] and not B.evaluate(loss, "2020-04-01")[0], "pérdida re-expresada"
    assert not B.evaluate(good, "2013-04-01")[0], "menos de 5 años no pasa"
    iv = B.intrinsic(info)
    assert iv > 1000 and B.shares_now(good, "2020-03-01") == (50.0, "2020-02-20")


def test_zip_and_names():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("CIK0000000042.json", json.dumps({"cik": 42, "entityName": "Acme Widgets Inc", **company()}))
        assert B.index_facts(p) == {42: "Acme Widgets Inc"}
        assert 42 in B.load_fundamentals(p, [42, 7])
    assert B.norm_name("ACME WIDGETS, INC. COMMON STOCK") == B.norm_name("Acme Widgets Inc") == "ACME WIDGETS"


def test_simulate():
    T = 6
    R = np.zeros((3, T))
    R[0, 1:] = 0.01            # acción 0 sube 1% diario
    R[1, 3:] = np.nan          # acción 1 deja de cotizar
    R[2, 1:] = 0.0             # "SPY" plano
    r, to = B.simulate(R, [[0, 1]], [0], spy_row=2, n_slots=4, cost=0.0)
    assert abs(r[1] - 0.25 * 0.01) < 1e-12 and to == [1.0]
    eq = np.prod(1 + r)
    assert abs(eq - (0.25 * 1.01 ** 5 + 0.75)) < 1e-9, eq


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
