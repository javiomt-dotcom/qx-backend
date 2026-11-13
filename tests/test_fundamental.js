// Pruebas del tablero fundamental (docs/fundamental/calc.js). Corre con: node tests/test_fundamental.js
const assert = require("assert");
const Q = require("../docs/fundamental/calc.js");
const near = (a, b, tol, msg) => assert(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);
let n = 0;
const t = (name, f) => { f(); n++; console.log("ok", name); };

t("splits: un split 4:1 se ajusta hacia atrás", () => {
  const sh = Q.normSplits({ 2018: 5000, 2019: 4900, 2020: 19000, 2021: 18500 });
  near(sh[2018], 20000, 1, "2018"); near(sh[2019], 19600, 1, "2019"); near(sh[2021], 18500, 1, "2021");
});

t("DCF: flujo constante sin crecimiento es una perpetuidad", () => {
  // g = gt = 0: valor = base / r
  const v = Q.dcfValue({ base: 100, g: 0, gt: 0, r: 0.1, years: 10, netDebt: 0, shares: 10 });
  near(v.equity, 1000, 1e-6, "valor"); near(v.perShare, 100, 1e-6, "por acción");
  const v2 = Q.dcfValue({ base: 100, g: 0, gt: 0, r: 0.1, years: 10, netDebt: 300, shares: 10 });
  near(v2.perShare, 70, 1e-6, "resta deuda neta");
  assert.strictEqual(Q.dcfValue({ base: -5, g: 0, gt: 0, r: 0.1, years: 10 }), null);
});

t("DCF al revés devuelve el crecimiento que reproduce el precio", () => {
  const p = { base: 100, g: 0.05, gt: 0.03, r: 0.1, years: 10, netDebt: 0, shares: 10 };
  const price = Q.dcfValue({ ...p, g: 0.12 }).perShare;
  near(Q.impliedGrowth(p, price).g, 0.12, 1e-4, "g implícito");
});

t("riesgo: un activo que se mueve 2x el mercado tiene beta 2", () => {
  const m = Array.from({ length: 500 }, (_, i) => 0.01 * Math.sin(i * 1.7) + 0.0004);
  const r = m.map((x) => 2 * x);
  const s = Q.riskStats(r, m, 0.04);
  near(s.beta, 2, 1e-9, "beta"); near(s.corr, 1, 1e-9, "correlación");
  near(s.vol, 2 * Q.riskStats(m, m, 0.04).vol, 1e-9, "volatilidad");
  assert(s.maxDD <= 0 && s.var95 < 0);
});

t("caída máxima", () => { near(Q.maxDrawdown([100, 120, 60, 130, 65]), -0.5, 1e-12, "dd"); });

t("fundamentales, Piotroski, Altman y Buffett con una empresa sintética", () => {
  const ys = ["2019", "2020", "2021", "2022", "2023", "2024"];
  const mk = (f) => Object.fromEntries(ys.map((y, i) => [y, f(i)]));
  const sec = { series: {
    rev: mk((i) => 1000 * 1.06 ** i), gp: mk((i) => 600 * 1.07 ** i), ebit: mk((i) => 250 * 1.07 ** i), ni: mk((i) => 180 * 1.07 ** i),
    ocf: mk((i) => 220 * 1.07 ** i), capex: mk(() => 40), da: mk(() => 30), tax: mk(() => 40), pretax: mk(() => 220), int: mk(() => 10),
    sh: mk((i) => 100 - i), assets: mk(() => 1500), liab: mk(() => 700), ca: mk((i) => 500 + 10 * i), cl: mk(() => 300),
    eq: mk(() => 800), re: mk(() => 400), cash: mk(() => 100), ltd: mk(() => 200), std: mk(() => 0),
  }, sharesOut: { v: 95 } };
  const f = Q.fundamentals(sec, 50, {}, "Beverages");
  near(f.mcap, 4750, 1e-6, "valor de mercado"); near(f.netDebt, 100, 1e-9, "deuda neta");
  assert(f.roic > 0.15 && f.roe > 0.2 && f.shChg5 < 0 && !f.financial);
  const p = Q.piotroski(sec); assert(p.score >= 6, "piotroski " + p.score);
  assert.strictEqual(Q.altman(sec, f.mcap).zone, "segura");
  const b = Q.buffett(sec, f.mcap, 10000);
  assert(b.quality, "pasa las 5 reglas de calidad"); assert.strictEqual(b.items[5].ok, true);
  assert(Q.isFinancial("Banking") && !Q.isFinancial("Technology"));
});

t("portafolio: recomendaciones detectan concentración y falta de renta fija", () => {
  const h = (symbol, value, sector, etf) => ({ symbol, value, cost: null, price: 1, bucket: Q.assetBucket(symbol, { sector, country: "US", etf }) });
  const holdings = [h("NVDA", 6000, "Semiconductors"), h("AMD", 2000, "Semiconductors"), h("VOO", 2000, null, true)];
  const recs = Q.recommendations({ holdings, total: 10000, cash: 0, stats: { beta: 1.6, sharpe: 0.5, maxDD: -0.45 }, spyStats: { sharpe: 0.9, maxDD: -0.25 }, profile: "moderado", risk: null });
  const titles = recs.map((r) => r.title).join(" | ");
  assert(/NVDA pesa 60%/.test(titles), titles); assert(/Tecnología/.test(titles), titles);
  assert(/Renta fija/.test(titles), titles); assert(/Beta 1.60/.test(titles), titles);
  assert(/Ya tienes NVDA/.test(titles), titles); assert.strictEqual(recs[0].level, "alerta");
  assert.strictEqual(Q.assetBucket("BND", {}).cls, "bonds");
});

t("portafolio: serie con pesos de hoy y aporte al riesgo suma 1", () => {
  const days = Array.from({ length: 300 }, (_, i) => new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10));
  const walk = (k) => { let x = 100; return days.map((_, i) => (x *= 1 + 0.01 * Math.sin(i * k) + 0.0003)); };
  const series = { SPY: { t: days, c: walk(1.1) }, A: { t: days, c: walk(1.1).map((x) => x * 2) }, B: { t: days, c: walk(2.3) } };
  const w = { A: 0.5, B: 0.4, __cash: 0.1 };
  const ps = Q.portfolioSeries(series, w);
  assert.strictEqual(ps.r.length, 299);
  const rc = Q.riskContrib(series, w, ps.syms);
  near(Object.values(rc.contrib).reduce((s, x) => s + x, 0), 1, 1e-9, "aportes");
});

console.log(`\n${n} pruebas OK`);
