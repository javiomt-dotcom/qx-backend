// Precios para la app QX: guarda las llaves de Alpaca en el servidor y sólo contesta a correos aprobados (qx_viewers).
// body: { symbols: ["AAPL", ...], opens: [{ s: "AAPL", d: "2026-10-20" }, ...] }
//  quotes[s] = precio actual, cierre previo, apertura y cierre de la última sesión
//    last: la última operación en IEX si tiene menos de 60 s; si no, el punto medio compra/venta de IEX
//    (cuando es más reciente y el diferencial es menor a 1%); src = "trade" | "mid"; age = segundos de antigüedad
//  opens["AAPL|2026-10-20"] = apertura de ese día (precio de compra cuando el usuario no lo escribe)
//  Si existe el secreto FINNHUB_KEY, también consulta Finnhub (/quote, gratis: 60 por minuto) y se queda con el
//  precio más reciente de las dos fuentes. Finnhub se guarda en memoria y se refresca por turnos, los más viejos
//  primero, sin pasar de FH_PER_MIN consultas por minuto; src = "finnhub" cuando gana.
// Desplegada en Supabase (proyecto jswqsaogomxyjtyhoucn) con verify_jwt; esta copia es la fuente.
import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });
const SYM = /^[A-Z][A-Z0-9.\-]{0,9}$/, DAY = /^\d{4}-\d{2}-\d{2}$/;
const FRESH_S = 60, MAX_SPREAD = 0.01;

// cliente y permiso se reutilizan mientras la función siga caliente: menos viajes por consulta
const ALLOW_TTL = 60_000;
const allowCache = new Map<string, number>();

// --- Finnhub: caché por símbolo y presupuesto de consultas por minuto (por instancia caliente) ---
const FH_PER_MIN = 50, FH_MAX_CALL = 8, FH_TTL_MS = 10_000;
const fhCache = new Map<string, { p: number; t: number; pc: number; got: number }>();
let fhWin = 0, fhUsed = 0, fhBlockedUntil = 0;

async function finnhub(symbols: string[], token: string) {
  const now = Date.now();
  if (now - fhWin >= 60_000) { fhWin = now; fhUsed = 0; }
  if (now < fhBlockedUntil) return;
  const stale = symbols.filter((s) => now - (fhCache.get(s)?.got ?? 0) > FH_TTL_MS)
    .sort((a, b) => (fhCache.get(a)?.got ?? 0) - (fhCache.get(b)?.got ?? 0))
    .slice(0, Math.max(0, Math.min(FH_MAX_CALL, FH_PER_MIN - fhUsed)));
  fhUsed += stale.length;
  await Promise.all(stale.map(async (s) => {
    try {
      const r = await fetch(`https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(s)}&token=${token}`,
                            { signal: AbortSignal.timeout(2500) });
      if (r.status === 429) { fhBlockedUntil = Date.now() + 30_000; return; }
      if (!r.ok) return;
      const q = await r.json();
      if (q && q.c > 0 && q.t > 0) fhCache.set(s, { p: q.c, t: q.t * 1000, pc: q.pc, got: Date.now() });
    } catch { /* tiempo agotado o red: se queda IEX */ }
  }));
}

function best(v: any, now: number) {
  const tp = v?.latestTrade?.p, tt = v?.latestTrade?.t ? Date.parse(v.latestTrade.t) : NaN;
  const bp = v?.latestQuote?.bp, ap = v?.latestQuote?.ap, qt = v?.latestQuote?.t ? Date.parse(v.latestQuote.t) : NaN;
  const tAge = isFinite(tt) ? (now - tt) / 1000 : Infinity;
  const qAge = isFinite(qt) ? (now - qt) / 1000 : Infinity;
  const mid = bp > 0 && ap > 0 && ap >= bp && (ap - bp) / ((ap + bp) / 2) < MAX_SPREAD ? (ap + bp) / 2 : null;
  if (tp && tAge <= FRESH_S) return { last: tp, src: "trade", age: Math.round(tAge) };
  if (mid && qAge < tAge) return { last: mid, src: "mid", age: Math.round(qAge) };
  if (tp) return { last: tp, src: "trade", age: isFinite(tAge) ? Math.round(tAge) : null };
  return { last: v?.minuteBar?.c ?? v?.dailyBar?.c, src: "bar", age: null };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const auth = req.headers.get("Authorization") ?? "";
  const ok = (allowCache.get(auth) ?? 0) > Date.now();
  if (!ok) {
    const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
      global: { headers: { Authorization: auth } },
    });
    const { data: allowed, error } = await sb.rpc("qx_allowed");
    if (error || allowed !== true) return json({ error: "no_autorizado" }, 403);
    allowCache.set(auth, Date.now() + ALLOW_TTL);
    if (allowCache.size > 200) allowCache.clear();
  }

  const key = Deno.env.get("ALPACA_KEY") ?? Deno.env.get("ALPACA_KEY_ID");
  const secret = Deno.env.get("ALPACA_SECRETO") ?? Deno.env.get("ALPACA_SECRET_KEY");
  if (!key || !secret) return json({ error: "faltan_llaves" }, 503);
  const H = { "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret };

  let body: any = {};
  try { body = await req.json(); } catch { /* cuerpo vacío */ }
  const fhKey = Deno.env.get("FINNHUB_KEY");
  const symbols = [...new Set((body.symbols ?? []).map((s: unknown) => String(s).toUpperCase()).filter((s: string) => SYM.test(s)))].slice(0, 80) as string[];
  const quotes: Record<string, unknown> = {};
  if (symbols.length) {
    const [r] = await Promise.all([
      fetch(`https://data.alpaca.markets/v2/stocks/snapshots?feed=iex&symbols=${encodeURIComponent(symbols.join(","))}`, { headers: H }),
      fhKey ? finnhub(symbols, fhKey) : Promise.resolve(),
    ]);
    if (!r.ok) return json({ error: `alpaca_${r.status}` }, 502);
    const now = Date.now();
    for (const [s, v] of Object.entries<any>(await r.json())) {
      let b: { last: number; src: string; age: number | null } = best(v, now);
      const f = fhCache.get(s);
      if (f && (now - f.t) / 1000 < (b.age ?? Infinity)) b = { last: f.p, src: "finnhub", age: Math.round((now - f.t) / 1000) };
      const prev = v?.prevDailyBar?.c;
      quotes[s] = { ...b, prev, open: v?.dailyBar?.o, close: v?.dailyBar?.c, day: v?.dailyBar?.t?.slice(0, 10) ?? null,
                    chg: b.last && prev ? b.last / prev - 1 : null, t: v?.latestTrade?.t ?? null };
    }
  }

  // aperturas de días pasados, agrupadas por fecha
  const opens: Record<string, number> = {};
  const byDay: Record<string, Set<string>> = {};
  for (const o of (body.opens ?? []).slice(0, 200)) {
    const s = String(o?.s ?? "").toUpperCase(), d = String(o?.d ?? "");
    if (SYM.test(s) && DAY.test(d)) (byDay[d] ??= new Set()).add(s);
  }
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  for (const [d, set] of Object.entries(byDay).slice(0, 40)) {
    const end = new Date(Date.parse(d + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
    for (const feed of d >= today ? ["iex"] : ["sip", "iex"]) {
      const r = await fetch(`https://data.alpaca.markets/v2/stocks/bars?timeframe=1Day&adjustment=raw&feed=${feed}&start=${d}&end=${end}&limit=1000&symbols=${encodeURIComponent([...set].join(","))}`, { headers: H });
      if (!r.ok) continue;
      const js = await r.json();
      for (const [s, bars] of Object.entries<any>(js.bars ?? {})) {
        const b = (bars as any[]).find((x) => String(x.t).slice(0, 10) === d);
        if (b) opens[`${s}|${d}`] = b.o;
      }
      break;
    }
  }
  return json({ quotes, opens, at: new Date().toISOString() });
});
