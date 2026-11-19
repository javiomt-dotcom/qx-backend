// Pruebas del copiloto (supabase/functions/fundamentals/copilot.ts) sin red ni llaves:
// limpieza del 10-K, partición por secciones, búsqueda BM25 y el flujo completo con la SEC y Claude simulados.
// Uso: node tests/test_copilot.js   (necesita el paquete "typescript", global o local)
const fs = require("fs"), path = require("path"), assert = require("assert");
let ts;
try { ts = require("typescript"); } catch (e) {
  try { ts = require(path.join(require("child_process").execSync("npm root -g").toString().trim(), "typescript")); }
  catch (e2) { console.log("sin typescript: se omiten las pruebas del copiloto"); process.exit(0); }
}

const src = fs.readFileSync(path.join(__dirname, "../supabase/functions/fundamentals/copilot.ts"), "utf8")
  .replace(/^import Anthropic from .*$/m, "const Anthropic = globalThis.__MockAnthropic;");
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

const calls = [];
globalThis.__MockAnthropic = class {
  constructor(o) { this.o = o; this.beta = { messages: { create: async (req) => {
    calls.push(req);
    if (req.output_config && req.output_config.format) return { content: [{ type: "text", text: JSON.stringify({ terms: ["supply chain", "export controls", "competition"] }) }], usage: { input_tokens: 300, output_tokens: 60 }, stop_reason: "end_turn" };
    return { content: [{ type: "text", text: "Sus riesgos principales son la cadena de suministro [1] y los controles de exportación [2].\n- Depende de pocos fabricantes [1]\n- Ventas a China limitadas [2]" }], usage: { input_tokens: 5000, output_tokens: 400 }, stop_reason: "end_turn", model: "claude-opus-5-5" };
  } } }; }
};
globalThis.Deno = { env: { get: (k) => (k === "ANTHROPIC_API_KEY" ? "test" : undefined) } };
const mod = { exports: {} };
new Function("module", "exports", "require", "fetch", js)(mod, mod.exports, require, async (url) => fakeFetch(url));
const { htmlToText, chunk, bm25, copilot } = mod.exports;

const filler = (w) => (w + " ").repeat(60);
const HTML = `<html><head><title>x</title></head><body><ix:header><ix:hidden>HIDDEN XBRL</ix:hidden></ix:header>
<p>Item 1A. Risk Factors 12</p><p>Item 7. Management's Discussion 40</p>
<p>Item 1. Business</p><p>We design GPUs &amp; accelerated computing platforms for data centers. ${filler("datacenter gaming platforms")}</p>
<p>Item 1A. Risk Factors</p><p>We depend on third parties for our supply chain and manufacturing capacity. ${filler("supply foundry capacity")}</p>
<p>New export controls restrict our sales to China&#8217;s customers. ${filler("export license china")}</p>
<table><tr><td>2025</td><td>$130,497</td><td>$60,922</td><td>(1,234)</td><td>12.5%</td></tr></table>
<p>Item 7. Management&#x2019;s Discussion and Analysis</p><p>Revenue grew strongly due to Data Center demand. ${filler("revenue growth demand")}</p></body></html>`;

function fakeFetch(url) {
  const ok = (body, isText) => ({ ok: true, status: 200, json: async () => body, text: async () => body });
  if (url.includes("submissions")) return ok({ filings: { recent: { form: ["8-K", "10-Q", "10-K"], accessionNumber: ["a", "b", "0001045810-25-000023"], filingDate: ["2025-05-01", "2025-05-28", "2025-02-26"], primaryDocument: ["x.htm", "y.htm", "nvda-20250126.htm"] } } });
  if (url.includes("Archives")) return ok(HTML);
  throw new Error("url inesperada " + url);
}

(async () => {
  const t = htmlToText(HTML);
  assert(!t.includes("HIDDEN XBRL"), "quita el encabezado XBRL oculto");
  assert(t.includes("GPUs & accelerated") && t.includes("China’s") && t.includes("Management’s"), "decodifica entidades");
  console.log("ok limpieza del HTML del 10-K");

  const ch = chunk(t, 600);
  assert(ch.some((c) => c.item.startsWith("Item 1A") && /supply chain/.test(c.text)), "riesgos quedan en Item 1A");
  assert(ch.some((c) => c.item.startsWith("Item 7 ") && /Revenue grew/.test(c.text)), "MD&A queda en Item 7");
  assert(!ch.some((c) => /130,497/.test(c.text) && c.text.length < 300), "descarta tablas de números");
  console.log(`ok partición por secciones (${ch.length} pedazos)`);

  const top = bm25(ch, ["export controls", "china"], 2).map((i) => ch[i]);
  assert(top.length && /export/i.test(top[0].text), "BM25 encuentra el pedazo de exportaciones");
  assert.deepStrictEqual(bm25(ch, ["zzzz"]), [], "sin coincidencias regresa vacío");
  console.log("ok búsqueda BM25");

  const mem = new Map();
  const deps = { cached: async (k, ttl, fn) => (mem.has(k) ? mem.get(k) : (mem.set(k, await fn()), mem.get(k))), cikMap: async () => ({ NVDA: 1045810 }), ua: {} };
  const r = await copilot("NVDA", "NVIDIA", "¿Cuáles son sus riesgos principales?", "Precio: $180.00", deps);
  assert.strictEqual(r.filing.form, "10-K");
  assert(r.filing.url.endsWith("/1045810/000104581025000023/nvda-20250126.htm"), "arma la URL del 10-K");
  assert(r.sources.length >= 1 && r.sources.every((s, i) => s.n === i + 1), "fuentes numeradas");
  assert(/\[1\]/.test(r.answer));
  assert.strictEqual(r.usage.input, 5300); assert.strictEqual(r.usage.output, 460);
  assert(Math.abs(r.usage.usd - (5300 * 4 + 460 * 20) / 1e6) < 1e-4, "costo con precios de Opus 5.5");
  const ans = calls[1];
  assert.strictEqual(ans.model, "claude-opus-5-5");
  assert.deepStrictEqual(ans.betas, ["server-side-fallback-2026-07-01"]); assert.strictEqual(ans.fallbacks, "default");
  assert(ans.system.includes("nunca digas que compre"), "regla educativa en el sistema");
  assert(ans.messages[0].content.includes("[1] (Item"), "manda extractos numerados con su sección");
  console.log("ok flujo completo con SEC y Claude simulados");

  const n = calls.length;
  await copilot("NVDA", "NVIDIA", "¿Cuáles son sus riesgos principales?", "Precio: $180.00", deps);
  assert.strictEqual(calls.length, n, "la misma pregunta sale de caché");
  console.log("ok caché de respuestas");

  await assert.rejects(copilot("ZZZZ", "Z", "hola mundo", "", deps), /SEC/);
  console.log("ok empresa sin reportes en la SEC");
  console.log("\n6 pruebas del copiloto OK");
})().catch((e) => { console.error("FALLÓ:", e.message); process.exit(1); });
