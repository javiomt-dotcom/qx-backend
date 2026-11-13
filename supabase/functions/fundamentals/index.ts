// Tablero fundamental QX (aparte del motor de trading). Junta datos de empresa para la página docs/fundamental/.
// Las llaves viven sólo aquí (secretos de Supabase): FINNHUB_KEY, ALPACA_KEY / ALPACA_SECRETO, ALPHAVANTAGE_KEY (opcional).
// Sólo contesta a correos aprobados (qx_allowed). verify_jwt va apagado porque la función revisa la sesión ella misma
// y así GET ?selftest=1 (sin datos de nadie, sólo si cada fuente responde) se puede revisar desde fuera.
//
// POST { action: "company", symbol }       -> perfil, métricas Finnhub, analistas, próximo reporte, estados EDGAR, precios 5 años
// POST { action: "history", symbols, years } -> cierres ajustados alineados con SPY + perfil corto de cada símbolo + tasa libre de riesgo
// POST { action: "brief", symbols }        -> resumen ligero por símbolo (favoritos)
// POST { action: "quotes", symbols }       -> último precio y cierre previo
// POST { action: "search", q }             -> búsqueda de símbolo por nombre (Finnhub)
// Caché en la tabla fa_cache (sólo service role).
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const SYM = /^[A-Z][A-Z0-9.\-]{0,9}$/;
const UA = { "User-Agent": Deno.env.get("SEC_USER_AGENT") ?? "QX Research research@qx-backend.dev", "Accept-Encoding": "gzip, deflate" };
const H = 3600_000, DAY = 24 * H;

const sbAdmin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});
const FH = Deno.env.get("FINNHUB_KEY") ?? "";
const AK = Deno.env.get("ALPACA_KEY") ?? Deno.env.get("ALPACA_KEY_ID") ?? "";
const AS = Deno.env.get("ALPACA_SECRETO") ?? Deno.env.get("ALPACA_SECRET_KEY") ?? "";
const AV = Deno.env.get("ALPHAVANTAGE_KEY") ?? "";
const APCA = { "APCA-API-KEY-ID": AK, "APCA-API-SECRET-KEY": AS };

// ---------- caché ----------
const mem = new Map<string, { t: number; d: unknown }>();
async function cached<T>(key: string, ttl: number, fn: () => Promise<T>): Promise<T> {
  const m = mem.get(key);
  if (m && Date.now() - m.t < ttl) return m.d as T;
  const { data } = await sbAdmin.from("fa_cache").select("data,fetched_at").eq("key", key).maybeSingle();
  if (data && Date.now() - Date.parse(data.fetched_at) < ttl) {
    mem.set(key, { t: Date.parse(data.fetched_at), d: data.data });
    return data.data as T;
  }
  try {
    const d = await fn();
    if (d != null) {
      mem.set(key, { t: Date.now(), d });
      if (mem.size > 300) mem.clear();
      await sbAdmin.from("fa_cache").upsert({ key, data: d, fetched_at: new Date().toISOString() });
    }
    return d;
  } catch (e) {
    if (data) return data.data as T;   // si la fuente falla, mejor lo viejo que nada
    throw e;
  }
}

// ---------- Finnhub (60 por minuto gratis) ----------
let fhBlocked = 0;
async function fh(path: string) {
  if (!FH) throw new Error("falta FINNHUB_KEY");
  if (Date.now() < fhBlocked) throw new Error("finnhub_429");
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(`https://finnhub.io/api/v1/${path}${sep}token=${FH}`, { signal: AbortSignal.timeout(8000) });
  if (r.status === 429) { fhBlocked = Date.now() + 20_000; throw new Error("finnhub_429"); }
  if (!r.ok) throw new Error(`finnhub_${r.status}`);
  return r.json();
}
const profile = (s: string) => cached(`fh:profile:${s}`, 7 * DAY, async () => {
  const p = await fh(`stock/profile2?symbol=${encodeURIComponent(s)}`);
  return p && p.ticker ? p : { ticker: s, empty: true };
});

// ---------- Alpaca: precios diarios ajustados ----------
async function alpacaBars(symbols: string[], startISO: string) {
  if (!AK || !AS) throw new Error("faltan llaves de Alpaca");
  const out: Record<string, { t: string; c: number }[]> = {};
  const end = new Date(Date.now() - 20 * 60_000).toISOString();
  for (const feed of ["sip", "iex"]) {
    let token = "", ok = true;
    for (let i = 0; i < 30; i++) {
      const u = `https://data.alpaca.markets/v2/stocks/bars?timeframe=1Day&adjustment=all&feed=${feed}&start=${startISO}` +
        `&end=${end}&limit=10000&symbols=${encodeURIComponent(symbols.join(","))}${token ? `&page_token=${token}` : ""}`;
      const r = await fetch(u, { headers: APCA, signal: AbortSignal.timeout(15000) });
      if (!r.ok) { ok = false; break; }
      const js = await r.json();
      for (const [s, bars] of Object.entries<any>(js.bars ?? {}))
        for (const b of bars as any[]) (out[s] ??= []).push({ t: String(b.t).slice(0, 10), c: b.c });
      token = js.next_page_token;
      if (!token) break;
    }
    if (ok) return out;
    for (const k of Object.keys(out)) delete out[k];
  }
  throw new Error("alpaca_bars");
}
const nyToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
async function closes(sym: string, years: number) {
  // un registro por símbolo y día: 5 años sirve para todo (portafolio pide 3)
  const all = await cached(`bars:${sym}:${nyToday()}`, 12 * H, async () => {
    const start = new Date(Date.now() - 5.05 * 365.25 * DAY).toISOString().slice(0, 10);
    const b = (await alpacaBars([sym], start))[sym] ?? [];
    return { t: b.map((x) => x.t), c: b.map((x) => x.c) };
  });
  const from = new Date(Date.now() - years * 365.25 * DAY).toISOString().slice(0, 10);
  const i = all.t.findIndex((d: string) => d >= from);
  return i < 0 ? { t: [], c: [] } : { t: all.t.slice(i), c: all.c.slice(i) };
}

async function snapshots(symbols: string[]) {
  const q: Record<string, { last: number; prev: number; chg: number | null; day: string | null }> = {};
  if (!symbols.length || !AK) return q;
  const r = await fetch(`https://data.alpaca.markets/v2/stocks/snapshots?feed=iex&symbols=${encodeURIComponent(symbols.join(","))}`,
                        { headers: APCA, signal: AbortSignal.timeout(8000) });
  if (!r.ok) return q;
  for (const [s, v] of Object.entries<any>(await r.json())) {
    const last = v?.latestTrade?.p ?? v?.dailyBar?.c, prev = v?.prevDailyBar?.c;
    q[s] = { last, prev, chg: last && prev ? last / prev - 1 : null, day: v?.dailyBar?.t?.slice(0, 10) ?? null };
  }
  // Finnhub como respaldo/actualización (cotización consolidada, no sólo IEX)
  if (FH) await Promise.all(symbols.slice(0, 15).map(async (s) => {
    try {
      const f = await fh(`quote?symbol=${encodeURIComponent(s)}`);
      if (f && f.c > 0) q[s] = { last: f.c, prev: f.pc, chg: f.pc ? f.c / f.pc - 1 : null, day: q[s]?.day ?? null };
    } catch { /* se queda Alpaca */ }
  }));
  return q;
}

// ---------- tasa libre de riesgo: FRED (CSV público, sin llave; respaldo Alpha Vantage), 1 vez al día ----------
async function fred() {
  const from = new Date(Date.now() - 30 * DAY).toISOString().slice(0, 10);
  const last = async (id: string) => {
    const r = await fetch(`https://fred.stlouisfed.org/graph/fredgraph.csv?id=${id}&cosd=${from}`, { signal: AbortSignal.timeout(8000) });
    if (!r.ok) return null;
    const rows = (await r.text()).trim().split(/\r?\n/).slice(1).map((l) => l.split(",")).filter((x) => x[1] && isFinite(Number(x[1])) && x[1] !== "");
    const x = rows[rows.length - 1];
    return x ? { v: Number(x[1]) / 100, d: x[0] } : null;
  };
  const [t3, t10] = await Promise.all([last("DGS3MO"), last("DGS10")]);
  return t3 ? { t3m: t3.v, t10y: t10?.v ?? null, date: t3.d, src: "FRED (Tesoro EE. UU.)" } : null;
}
async function riskFree() {
  try {
    return await cached(`rf:${nyToday()}`, DAY, async () => {
      try { const t = await fred(); if (t) return t; } catch { /* sigue Alpha Vantage */ }
      if (!AV) return null;
      const get = async (m: string) => {
        const r = await fetch(`https://www.alphavantage.co/query?function=TREASURY_YIELD&interval=daily&maturity=${m}&apikey=${AV}`);
        const js = await r.json();
        const v = (js.data ?? []).find((x: any) => x.value && x.value !== ".");
        return v ? { v: Number(v.value) / 100, d: v.date } : null;
      };
      const [t3, t10] = [await get("3month"), await get("10year")];
      return t3 || t10 ? { t3m: t3?.v ?? null, t10y: t10?.v ?? null, date: t3?.d ?? t10?.d, src: "Alpha Vantage" } : null;
    }) ?? { t3m: null, t10y: null, date: null };
  } catch { return { t3m: null, t10y: null, date: null }; }
}

// ---------- SEC EDGAR ----------
const CONCEPTS: Record<string, string[]> = {
  rev: ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
  gp: ["GrossProfit"],
  cogs: ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
  ebit: ["OperatingIncomeLoss"],
  ni: ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
  int: ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt", "InterestPaidNet"],
  tax: ["IncomeTaxExpenseBenefit"],
  pretax: ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
           "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
  da: ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet", "Depreciation"],
  ocf: ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
  capex: ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
  div: ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
  buyback: ["PaymentsForRepurchaseOfCommonStock"],
  sh: ["WeightedAverageNumberOfDilutedSharesOutstanding"],
  // saldos (instantes)
  assets: ["Assets"],
  liab: ["Liabilities"],
  ca: ["AssetsCurrent"],
  cl: ["LiabilitiesCurrent"],
  eq: ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
  re: ["RetainedEarningsAccumulatedDeficit"],
  cash: ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
  sti: ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
  ltd: ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
  std: ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper"],
};
const INSTANT = new Set(["assets", "liab", "ca", "cl", "eq", "re", "cash", "sti", "ltd", "std"]);

function annualSeries(facts: any, key: string) {
  const gaap = facts?.facts?.["us-gaap"] ?? {};
  // el concepto con el año fiscal más reciente gana (las empresas cambian de etiqueta con los años)
  let best: Record<string, { v: number; f: string }> = {}, bestFy = "";
  for (const concept of CONCEPTS[key]) {
    const units = gaap[concept]?.units;
    if (!units) continue;
    const vals = units.USD ?? units.shares ?? Object.values(units)[0] ?? [];
    const out: Record<string, { v: number; f: string }> = {};
    for (const x of vals as any[]) {
      if (!String(x.form ?? "").startsWith("10-K") || !x.end || !x.filed) continue;
      const end = Date.parse(x.end);
      if (!INSTANT.has(key)) {
        if (!x.start) continue;
        const days = (end - Date.parse(x.start)) / DAY;
        if (days < 340 || days > 390) continue;
      }
      const fy = String(new Date(end - 180 * DAY).getUTCFullYear());
      if (!out[fy] || x.filed > out[fy].f) out[fy] = { v: Number(x.val), f: x.filed };
    }
    const fys = Object.keys(out).sort();
    if (fys.length && fys[fys.length - 1] > bestFy) { best = out; bestFy = fys[fys.length - 1]; }
  }
  const res: Record<string, number> = {};
  for (const fy of Object.keys(best).sort().slice(-11)) res[fy] = best[fy].v;
  return res;
}

const cikMap = () => cached("sec:tickers", 7 * DAY, async () => {
  const r = await fetch("https://www.sec.gov/files/company_tickers.json", { headers: UA });
  if (!r.ok) throw new Error(`sec_tickers_${r.status}`);
  const js = await r.json();
  const m: Record<string, number> = {};
  for (const v of Object.values<any>(js)) m[String(v.ticker).toUpperCase()] = v.cik_str;
  return m;
});

async function secDirect(sym: string) {
  const m = await cikMap();
  const cik = m[sym] ?? m[sym.replace(".", "-")];
  if (!cik) return { none: true };
  const r = await fetch(`https://data.sec.gov/api/xbrl/companyfacts/CIK${String(cik).padStart(10, "0")}.json`, { headers: UA });
  if (r.status === 404) return { none: true, cik };
  if (!r.ok) throw new Error(`sec_facts_${r.status}`);
  const facts = await r.json();
  const series: Record<string, Record<string, number>> = {};
  for (const k of Object.keys(CONCEPTS)) series[k] = annualSeries(facts, k);
  const cover = (facts?.facts?.dei?.EntityCommonStockSharesOutstanding?.units?.shares ?? [])
    .filter((x: any) => x.end && x.filed).sort((a: any, b: any) => (a.filed < b.filed ? -1 : 1));
  const last = cover[cover.length - 1];
  return { cik, name: facts.entityName, series, sharesOut: last ? { v: Number(last.val), d: last.end } : null, src: "SEC EDGAR" };
}

// Respaldo: Finnhub "financials as reported" (los mismos 10-K de la SEC, ya en XBRL)
async function secViaFinnhub(sym: string) {
  const js = await fh(`stock/financials-reported?symbol=${encodeURIComponent(sym)}&freq=annual`);
  const data = (js?.data ?? []).filter((d: any) => String(d.form ?? "").startsWith("10-K"));
  if (!data.length) return { none: true };
  const series: Record<string, Record<string, number>> = {};
  for (const k of Object.keys(CONCEPTS)) series[k] = {};
  let sharesOut: { v: number; d: string } | null = null;
  for (const d of data) {
    const fy = String(d.year);
    const items: Record<string, number> = {};
    for (const part of ["bs", "ic", "cf"]) for (const it of d.report?.[part] ?? []) {
      const c = String(it.concept ?? "").replace(/^us-gaap[_:]/, "").replace(/^dei[_:]/, "");
      if (typeof it.value === "number" && !(c in items)) items[c] = it.value;
    }
    for (const [k, list] of Object.entries(CONCEPTS)) {
      const c = list.find((x) => x in items);
      if (c != null && series[k][fy] == null) series[k][fy] = items[c];
    }
    if (!sharesOut && items.EntityCommonStockSharesOutstanding) sharesOut = { v: items.EntityCommonStockSharesOutstanding, d: d.endDate?.slice(0, 10) };
  }
  for (const k of Object.keys(series)) {
    const keep: Record<string, number> = {};
    for (const fy of Object.keys(series[k]).sort().slice(-11)) keep[fy] = series[k][fy];
    series[k] = keep;
  }
  return { cik: js?.cik ?? null, name: null, series, sharesOut, src: "SEC vía Finnhub" };
}

async function edgar(sym: string) {
  return await cached(`sec:facts:${sym}`, 3 * DAY, async () => {
    try {
      const d: any = await secDirect(sym);
      if (!d.none) return d;
    } catch { /* la SEC a veces bloquea servidores en la nube: usa Finnhub */ }
    return await secViaFinnhub(sym);
  });
}

// ---------- acciones ----------
async function company(sym: string) {
  const errors: string[] = [];
  const safe = async <T>(name: string, p: Promise<T>): Promise<T | null> => {
    try { return await p; } catch (e) { errors.push(`${name}: ${(e as Error).message}`); return null; }
  };
  const today = nyToday();
  const to = new Date(Date.now() + 120 * DAY).toISOString().slice(0, 10);
  const [prof, metric, rec, earn, sec, px, spy, q, rf] = await Promise.all([
    safe("perfil", profile(sym)),
    safe("métricas", cached(`fh:metric:${sym}`, 12 * H, async () => {
      const m = await fh(`stock/metric?symbol=${encodeURIComponent(sym)}&metric=all`);
      return m?.metric ?? {};
    })),
    safe("analistas", cached(`fh:rec:${sym}`, DAY, () => fh(`stock/recommendation?symbol=${encodeURIComponent(sym)}`))),
    safe("reportes", cached(`fh:earn:${sym}:${today}`, DAY, async () =>
      (await fh(`calendar/earnings?from=${today}&to=${to}&symbol=${encodeURIComponent(sym)}`))?.earningsCalendar ?? [])),
    safe("EDGAR", edgar(sym)),
    safe("precios", closes(sym, 5)),
    safe("precios SPY", closes("SPY", 5)),
    safe("cotización", snapshots([sym])),
    riskFree(),
  ]);
  return {
    symbol: sym, profile: prof, metric, recommendation: Array.isArray(rec) ? rec.slice(0, 4) : [],
    earnings: earn ?? [], sec, prices: px, spy, quote: q?.[sym] ?? null, rf, errors, at: new Date().toISOString(),
  };
}

async function history(symbols: string[], years: number) {
  const errors: string[] = [];
  const all = [...new Set(["SPY", ...symbols])];
  const series: Record<string, { t: string[]; c: number[] }> = {};
  const profiles: Record<string, unknown> = {};
  await Promise.all(all.map(async (s) => {
    try { series[s] = await closes(s, years); } catch (e) { errors.push(`${s}: ${(e as Error).message}`); }
  }));
  for (const s of symbols) {   // de uno en uno: cuida el límite de Finnhub
    try {
      const p: any = await profile(s);
      profiles[s] = { name: p.name ?? null, sector: p.finnhubIndustry ?? null, country: p.country ?? null,
                      mcap: p.marketCapitalization ?? null, logo: p.logo ?? null, etf: !!p.empty };
    } catch (e) { profiles[s] = { etf: null }; errors.push(`perfil ${s}: ${(e as Error).message}`); }
  }
  const [quotes, rf] = await Promise.all([snapshots(symbols), riskFree()]);
  return { series, profiles, quotes, rf, errors, at: new Date().toISOString() };
}

// Resumen ligero para la lista de favoritos: perfil, métricas clave, estados (para el DCF rápido) y precio
const BRIEF_KEYS = ["beta", "peTTM", "forwardPE", "pb", "psTTM", "evEbitdaTTM", "pfcfShareTTM", "dividendYieldIndicatedAnnual",
  "52WeekHigh", "52WeekLow", "52WeekPriceReturnDaily", "roeTTM", "netProfitMarginTTM", "revenueGrowthTTMYoy", "marketCapitalization"];
async function brief(symbols: string[]) {
  const errors: string[] = [];
  const out: Record<string, unknown> = {};
  for (const s of symbols) {   // de uno en uno: cuida el límite de Finnhub
    const row: any = {};
    try {
      const p: any = await profile(s);
      row.profile = { name: p.name ?? null, sector: p.finnhubIndustry ?? null, logo: p.logo ?? null, etf: !!p.empty };
      const m: any = await cached(`fh:metric:${s}`, 12 * H, async () => (await fh(`stock/metric?symbol=${encodeURIComponent(s)}&metric=all`))?.metric ?? {});
      row.metric = Object.fromEntries(BRIEF_KEYS.map((k) => [k, m?.[k] ?? null]));
      if (!p.empty) {
        try { const e: any = await edgar(s); if (e && !e.none) row.sec = { series: e.series, sharesOut: e.sharesOut }; } catch { /* sin DCF */ }
      }
    } catch (e) { errors.push(`${s}: ${(e as Error).message}`); }
    out[s] = row;
  }
  const quotes = await snapshots(symbols);
  return { items: out, quotes, errors, at: new Date().toISOString() };
}

async function selftest() {
  return await cached("selftest:v4", 10 * 60_000, async () => {
    const r: Record<string, string> = {};
    const t = async (n: string, f: () => Promise<string>) => {
      try { r[n] = await f(); } catch (e) { r[n] = "error: " + (e as Error).message; }
    };
    await t("finnhub", async () => {
      const m = await fh("stock/metric?symbol=AAPL&metric=all");
      return `ok (${Object.keys(m?.metric ?? {}).length} métricas)`;
    });
    await t("finnhub_keys", async () => Object.keys((await fh("stock/metric?symbol=MSFT&metric=all"))?.metric ?? {}).sort().join(","));
    await t("edgar", async () => {
      const e: any = await edgar("AAPL");
      return e?.series ? `ok ${e.src} (ventas ${Object.keys(e.series.rev ?? {}).join(" ")}; flujo ${Object.keys(e.series.ocf ?? {}).length} años; acciones ${JSON.stringify(e.sharesOut)})` : "sin datos";
    });
    await t("alpaca", async () => { const c = await closes("SPY", 5); return `ok (${c.t.length} días, último ${c.t[c.t.length - 1]})`; });
    await t("rf", async () => JSON.stringify(await riskFree()));
    return r;
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const url = new URL(req.url);
  if (req.method === "GET") {
    if (url.searchParams.get("selftest")) return json(await selftest());
    return json({ ok: true });
  }

  // permiso: sesión de un correo aprobado
  const auth = req.headers.get("Authorization") ?? "";
  const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: auth } }, auth: { persistSession: false },
  });
  const { data: allowed, error } = await sb.rpc("qx_allowed");
  if (error || allowed !== true) return json({ error: "no_autorizado" }, 403);

  let body: any = {};
  try { body = await req.json(); } catch { /* vacío */ }
  const clean = (x: unknown) => String(x ?? "").trim().toUpperCase();
  try {
    switch (body.action) {
      case "company": {
        const s = clean(body.symbol);
        if (!SYM.test(s)) return json({ error: "símbolo inválido" }, 400);
        return json(await company(s));
      }
      case "history": {
        const syms = [...new Set((body.symbols ?? []).map(clean).filter((s: string) => SYM.test(s)))].slice(0, 40) as string[];
        const years = Math.min(5, Math.max(1, Number(body.years) || 3));
        return json(await history(syms, years));
      }
      case "brief": {
        const syms = [...new Set((body.symbols ?? []).map(clean).filter((s: string) => SYM.test(s)))].slice(0, 30) as string[];
        return json(await brief(syms));
      }
      case "quotes": {
        const syms = [...new Set((body.symbols ?? []).map(clean).filter((s: string) => SYM.test(s)))].slice(0, 60) as string[];
        return json({ quotes: await snapshots(syms), at: new Date().toISOString() });
      }
      case "search": {
        const q = String(body.q ?? "").trim().slice(0, 40);
        if (q.length < 1) return json({ result: [] });
        const r = await cached(`fh:search:${q.toLowerCase()}`, 7 * DAY, () => fh(`search?q=${encodeURIComponent(q)}&exchange=US`));
        const result = (r?.result ?? []).filter((x: any) => SYM.test(x.symbol) && !x.symbol.includes("."))
          .slice(0, 10).map((x: any) => ({ symbol: x.symbol, name: x.description, type: x.type }));
        return json({ result });
      }
      default:
        return json({ error: "acción desconocida" }, 400);
    }
  } catch (e) {
    return json({ error: (e as Error).message }, 502);
  }
});
