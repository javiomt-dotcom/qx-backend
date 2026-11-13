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

t("DCF de dos etapas: ventas de 12 meses, margen, crecimiento que baja y tasa por beta", () => {
  const sec = { series: {
      rev: { 2021: 100, 2022: 130, 2023: 170, 2024: 220, 2025: 290, 2026: 380 },
      ocf: { 2024: 90, 2025: 120, 2026: 160 }, capex: { 2024: 2, 2025: 4, 2026: 8 } },
    ttm: { rev: { v: 500, end: "2026-07-26", fyEnd: "2026-01-25" }, ocf: { v: 210, end: "2026-07-26" }, capex: { v: 10, end: "2026-07-26" }, end: "2026-07-26" } };
  const p = Q.dcfDefaults(sec, { netDebt: -20, shares: 10 }, { rf10: 0.05, beta: 1 });
  assert.strictEqual(p.rev0, 500, "usa ventas de 12 meses"); assert(/12 meses/.test(p.revFrom));
  near(p.margin, 0.4, 0.002, "mediana de márgenes");
  near(p.g, 0.06 + 0.4 * (p.gRaw - 0.06), 1e-12, "40% del exceso sobre 6%"); assert(p.gRaw > 0.25);
  const nv = Q.dcfDefaults({ series: { rev: { 2021: 17, 2022: 27, 2023: 27, 2024: 61, 2025: 130, 2026: 216 } } }, null, {});
  near(nv.g, 0.25, 1e-12, "tope de 25%");
  const corta = Q.dcfDefaults({ series: { rev: { 2023: 87, 2024: 170, 2025: 252 } } }, null, {});
  assert.strictEqual(corta.gYears, 2); assert(corta.gRaw > 0.6 && corta.g > 0.2, "con 3 años de ventas usa 2 años de crecimiento");
  near(p.r, 0.095, 1e-12, "5% + 1.0 × 4.5%");
  near(Q.discountRate(0.05, 3).r, 0.12, 1e-12, "tope 12%"); near(Q.discountRate(0.03, 0).r, 0.08, 1e-12, "piso 8%");
  const v = Q.dcfValue(p);
  assert.strictEqual(v.rows.length, 10);
  near(v.rows[4].g, p.g, 1e-12, "año 5"); near(v.rows[9].g, 0.03, 1e-12, "año 10 = perpetuo"); near(v.rows[7].g, p.g + (0.03 - p.g) * 3 / 5, 1e-12, "año 8");
  near(v.rows[0].fcf, 500 * 0.4 * (1 + p.g), 1e-9, "flujo año 1");
  near(v.equity, v.pv + v.tv + 20, 1e-9, "suma el efectivo neto");
  const sc = Q.dcfScenarios(p);
  assert(sc.bear.perShare < sc.base.perShare && sc.base.perShare < sc.bull.perShare, "escenarios ordenados");
  const price = v.perShare * 0.8;
  const ig = Q.impliedGrowth(p, price);
  near(Q.dcfValue({ ...p, g: ig.g }).perShare, price, 1e-4, "crecimiento implícito reproduce el precio");
  const pr = Q.profitability({ series: { ni: { 2022: 10, 2023: 12, 2024: 15 }, eq: { 2022: 100, 2023: 100, 2024: 100 }, assets: { 2022: 200, 2023: 200, 2024: 300 },
    ebit: { 2022: 20, 2023: 20, 2024: 20 }, ltd: { 2024: 50 }, cash: { 2024: 50 } } });
  near(pr.roe[2], 0.15, 1e-12, "ROE"); near(pr.roa[2], 0.05, 1e-12, "ROA"); near(pr.roic[2], 20 * 0.79 / 100, 1e-12, "ROIC"); near(pr.roeMed, 0.12, 1e-12, "mediana");
  // método conservador (backtest Buffett) sigue igual
  const c = Q.dcfConservative(sec, { netDebt: 0, shares: 10 });
  near(c.base, (88 + 116 + 152) / 3, 1e-9, "promedio 3 años"); near(c.g, 0.08, 1e-12, "tope 8%");
});

const P = require("../docs/fundamental/profile.js");
const answersFor = (level) => {   // 0 = la opción más prudente en todo, 1 = la más arriesgada
  const a = {};
  for (const q of P.QUESTIONS) {
    if (q.kind === "num") a[q.id] = q.id === "f6" ? 25000 : 300;
    else if (q.kind === "many") a[q.id] = [0, 1];
    else {
      const order = q.opts.map((o, i) => [o[1], i]).sort((x, y) => x[0] - y[0]);
      a[q.id] = (level ? order[order.length - 1] : order[0])[1];
    }
  }
  return a;
};

t("perfil: respuestas prudentes dan conservador y arriesgadas dan agresivo", () => {
  assert.strictEqual(P.evaluate(answersFor(0)).profile, "conservador");
  const ev = P.evaluate(answersFor(1));
  assert.strictEqual(ev.profile, "agresivo"); assert.strictEqual(ev.amount, 25000); assert.strictEqual(ev.monthly, 300);
  assert.strictEqual(P.missing({}).length, P.QUESTIONS.filter((q) => q.kind === "one").length);
  assert.strictEqual(P.missing(answersFor(1)).length, 0);
});

t("perfil: el plazo corto y la falta de fondo de emergencia limitan el riesgo", () => {
  const a = answersFor(1);
  assert.strictEqual(P.evaluate({ ...a, o2: 0 }).profile, "conservador");
  assert.strictEqual(P.evaluate({ ...a, o2: 1 }).profile, "moderado_conservador");
  const sinFondo = P.evaluate({ ...a, f2: 0 });
  assert.strictEqual(sinFondo.profile, "moderado"); assert(sinFondo.caps.some((n) => /emergencia/.test(n)) && sinFondo.notes.some((n) => /3 a 6 meses/.test(n)));
});

t("portafolios: 4 por perfil, pesos suman 100% y siguen la mezcla del perfil", () => {
  for (const lvl of [0, 1]) {
    const a = answersFor(lvl), ev = P.evaluate(a), ps = P.buildPortfolios(ev, a);
    assert.strictEqual(ps.length, 4); assert.strictEqual(ps[0].id, "sp");
    for (const p of ps) {
      near(Object.values(p.w).reduce((s, x) => s + x, 0), 1, 1e-9, p.name);
      for (const s of Object.keys(p.w)) assert(P.ASSET[s], "ETF sin clase: " + s);
    }
    near(ps[0].mix.eq, P.MIX[ev.profile].eq, 0.011, "acciones del pegado al S&P"); near(ps[0].w.VOO, ps[0].mix.eq, 1e-9, "acciones 100% VOO");
  }
  const a = answersFor(1), ps = P.buildPortfolios(P.evaluate(a), a);
  assert.strictEqual(ps[3].id, "sat"); near(ps[3].w.JQUA * 2, ps[3].sat, 0.011, "satélite: mitad JQUA");
  assert(ps[3].sat <= 0.2 + 1e-9, "satélite máximo 20%");
  assert(!ps.some((p) => p.w.QUAL || p.w.MOAT || p.w.VB), "sin QUAL, MOAT ni VB");
});

t("referencia justa y baja volatilidad en perfiles cautos", () => {
  const a = answersFor(1), q = P.evaluate(a);
  for (const prof of P.PROFILE_ORDER) {
    const ev = P.withProfile(q, prof), ref = P.referenceWeights(ev, a);
    near(Object.values(ref).reduce((s, x) => s + x, 0), 1, 1e-9, "referencia suma 100% " + prof);
    near(ref.VOO, P.MIX[prof].eq, 0.011, "acciones de la referencia " + prof);
    const ps = P.buildPortfolios(ev, a), cautious = ["conservador", "moderado_conservador", "moderado"].includes(prof);
    assert.strictEqual(!!ps[1].w.USMV, cautious, "USMV en el sencillo sólo si es cauto: " + prof);
    for (const p of ps) near(Object.values(p.w).reduce((s, x) => s + x, 0), 1, 1e-9, p.name + " " + prof);
  }
});

t("perfil a mano: cambia la mezcla y avisa contra el cuestionario; sin cuestionario usa supuestos generales", () => {
  const a = answersFor(1), q = P.evaluate(a);
  const m = P.withProfile(q, "conservador");
  assert.strictEqual(m.profile, "conservador"); assert.strictEqual(m.idx, 0); assert.strictEqual(m.quizProfile, q.profile); assert.strictEqual(m.manual, q.profile !== "conservador");
  near(P.buildPortfolios(m, a)[0].mix.eq, P.MIX.conservador.eq, 0.011, "acciones conservador");
  assert.strictEqual(P.withProfile(q, q.profile).manual, false);
  const n = P.withProfile(null, "agresivo");
  assert(n.noQuiz && n.manual && n.level === 1 && n.amount === 10000);
  const ps = P.buildPortfolios(n, {});
  assert.strictEqual(ps.length, 4); near(ps[0].mix.eq, P.MIX.agresivo.eq, 0.011, "acciones agresivo");
  for (const p of ps) near(Object.values(p.w).reduce((s, x) => s + x, 0), 1, 1e-9, p.name);
  assert.strictEqual(P.withProfile(q, "otro"), q);
});

const fakeSeries = (days, f) => ({ t: days, c: days.map((_, i) => 100 * f(i)) });
const bdays = Array.from({ length: 2600 }, (_, i) => new Date(Date.UTC(2016, 0, 4 + Math.floor(i * 7 / 5)))).map((d) => d.toISOString().slice(0, 10));

t("simulación histórica: aportes, rendimiento compuesto y SPY con las mismas fechas", () => {
  const S = { SPY: fakeSeries(bdays, (i) => 1.0003 ** i), A: fakeSeries(bdays, (i) => 1.0002 ** i), B: fakeSeries(bdays, () => 1) };
  const bt = P.backtest(S, { A: 0.5, B: 0.5 }, { initial: 1000, monthly: 100 });
  assert(bt && bt.months > 100);
  near(bt.contributed, 1000 + 100 * bt.months, 1e-6, "aportado");
  assert(bt.final > bt.contributed && bt.finalSpy > bt.final, "SPY sube más rápido");
  assert(bt.maxDD > -1e-9, "sin caídas");
  const solo = P.backtest(S, { A: 1 }, { initial: 1000 });
  near(solo.cagr, P.backtest(S, { A: 0.5, B: 0.5 }, { initial: 1000 }).cagr * 2, 0.01, "mitad en efectivo, mitad del rendimiento (aprox.)");
});

t("Monte Carlo: percentiles ordenados y efectivo puro sin pérdidas", () => {
  let x = 100; const walk = bdays.map((_, i) => (x *= 1 + 0.012 * Math.sin(i * 0.9) + 0.0003));
  const S = { VTI: { t: bdays, c: walk }, BND: fakeSeries(bdays, (i) => 1 + 0.00005 * i), BIL: fakeSeries(bdays, (i) => 1 + 0.0001 * i) };
  const mc = P.monteCarlo(S, { VTI: 0.6, BND: 0.4 }, { years: 10, initial: 10000, monthly: 0, paths: 500, rf: 0.04 });
  assert(mc.p10 < mc.p50 && mc.p50 < mc.p90, "percentiles");
  assert.strictEqual(mc.bands.length, 11);
  near(mc.expRet, 0.6 * 0.065 + 0.4 * 0.043, 1e-12, "rendimiento esperado");
  const cash = P.monteCarlo(S, { BIL: 1 }, { years: 5, initial: 1000, paths: 200, rf: 0.04 });
  assert(cash.probBelow < 0.05 && cash.probDD30 === 0, "efectivo");
  const again = P.monteCarlo(S, { VTI: 0.6, BND: 0.4 }, { years: 10, initial: 10000, monthly: 0, paths: 500, rf: 0.04 });
  assert.strictEqual(again.p50, mc.p50, "misma semilla, mismo resultado");
});

t("mezcla actual por clase de activo", () => {
  const hs = [{ symbol: "VOO", value: 600 }, { symbol: "BND", value: 200 }, { symbol: "AAPL", value: 100, prof: { country: "US" } }];
  const m = P.mixOf(hs, 100, Q.assetBucket);
  near(m.eq, 0.7, 1e-12, "acciones"); near(m.bond, 0.2, 1e-12, "bonos"); near(m.cash, 0.1, 1e-12, "efectivo");
});

console.log(`\n${n} pruebas OK`);
