// QX Fundamental: cálculos puros (sin red ni pantalla). Los usa la página y las pruebas (node tests/test_fundamental.js).
// Todo es educativo: nada de esto es una recomendación de compra o venta.
(function (root) {
  "use strict";
  const num = (x) => (typeof x === "number" && isFinite(x) ? x : null);
  const years = (o) => Object.keys(o || {}).sort();
  const lastY = (o) => { const y = years(o); return y.length ? y[y.length - 1] : null; };
  const val = (o, y) => (o && y != null ? num(o[y]) : null);
  const lastV = (o) => val(o, lastY(o));
  const mean = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : null);
  const median = (a) => { if (!a.length) return null; const b = [...a].sort((x, y) => x - y), m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  const clip = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
  const cagr = (a, b, n) => (a > 0 && b > 0 && n > 0 ? Math.pow(b / a, 1 / n) - 1 : null);

  // Acciones diluidas: la SEC no ajusta por splits; si un año brinca x2 o más (o cae a la mitad) se toma como split.
  function normSplits(sh) {
    const ys = years(sh), out = {};
    if (!ys.length) return out;
    const f = {};
    let factor = 1;
    for (let i = ys.length - 1; i >= 0; i--) {
      f[ys[i]] = factor;
      if (i > 0) {
        const a = sh[ys[i - 1]], b = sh[ys[i]];
        if (a > 0 && b > 0) {
          const r = b / a;
          if (r >= 1.8 || r <= 0.55) {
            const cands = [2, 3, 4, 5, 7, 8, 10, 15, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10];
            const best = cands.reduce((p, c) => (Math.abs(Math.log(r / c)) < Math.abs(Math.log(r / p)) ? c : p), cands[0]);
            factor *= best;
          }
        }
      }
    }
    for (const y of ys) out[y] = sh[y] * f[y];
    return out;
  }

  // ---------- rendimientos y riesgo ----------
  function returns(c) { const r = []; for (let i = 1; i < c.length; i++) r.push(c[i - 1] > 0 ? c[i] / c[i - 1] - 1 : 0); return r; }
  function std(a) { const m = mean(a); if (a.length < 2) return null; return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)); }
  function cov(a, b) { const n = Math.min(a.length, b.length); if (n < 2) return null; const ma = mean(a.slice(0, n)), mb = mean(b.slice(0, n)); let s = 0; for (let i = 0; i < n; i++) s += (a[i] - ma) * (b[i] - mb); return s / (n - 1); }
  function corr(a, b) { const c = cov(a, b), sa = std(a), sb = std(b); return c != null && sa && sb ? c / (sa * sb) : null; }
  function maxDrawdown(c) { let peak = -Infinity, dd = 0; for (const x of c) { peak = Math.max(peak, x); dd = Math.min(dd, x / peak - 1); } return dd; }

  // Alinea dos series {t, c} por fecha
  function align(a, b) {
    const m = new Map(b.t.map((d, i) => [d, b.c[i]]));
    const t = [], ca = [], cb = [];
    a.t.forEach((d, i) => { if (m.has(d)) { t.push(d); ca.push(a.c[i]); cb.push(m.get(d)); } });
    return { t, a: ca, b: cb };
  }
  function monthEnds(t, c) {
    const out = [];
    for (let i = 0; i < t.length; i++) if (i === t.length - 1 || t[i].slice(0, 7) !== t[i + 1].slice(0, 7)) out.push(c[i]);
    return out;
  }
  function sliceYears(s, y) {
    if (!s || !s.t || !s.t.length) return { t: [], c: [] };
    const end = new Date(s.t[s.t.length - 1] + "T00:00:00Z"); end.setUTCDate(end.getUTCDate() - Math.round(y * 365.25));
    const from = end.toISOString().slice(0, 10), i = s.t.findIndex((d) => d >= from);
    return i < 0 ? { t: [], c: [] } : { t: s.t.slice(i), c: s.c.slice(i) };
  }

  // Estadísticas de riesgo de una serie de rendimientos diarios r contra el mercado m (mismas fechas). rf anual.
  function riskStats(r, m, rf) {
    rf = num(rf) ?? 0.04;
    const n = r.length;
    if (n < 40) return null;
    const rfd = Math.pow(1 + rf, 1 / 252) - 1;
    const eq = [1]; for (const x of r) eq.push(eq[eq.length - 1] * (1 + x));
    const yrs = n / 252, total = eq[eq.length - 1] - 1;
    const ann = Math.pow(1 + total, 1 / yrs) - 1;
    const sd = std(r), vol = sd * Math.sqrt(252);
    const ex = r.map((x) => x - rfd);
    const sharpe = vol ? (mean(ex) * 252) / vol : null;
    const down = Math.sqrt(mean(ex.map((x) => Math.min(0, x) ** 2))) * Math.sqrt(252);
    const sortino = down ? (mean(ex) * 252) / down : null;
    const mdd = maxDrawdown(eq);
    const sorted = [...r].sort((a, b) => a - b), var95 = sorted[Math.floor(0.05 * n)];
    const out = { n, years: yrs, total, ann, vol, sharpe, sortino, maxDD: mdd, calmar: mdd < 0 ? ann / -mdd : null, var95 };
    if (m && m.length === n) {
      const vm = cov(m, m), beta = vm ? cov(r, m) / vm : null;
      const exm = m.map((x) => x - rfd);
      const alpha = beta != null ? (mean(ex) - beta * mean(exm)) * 252 : null;
      const c = corr(r, m);
      const diff = r.map((x, i) => x - m[i]), te = std(diff) * Math.sqrt(252);
      const up = m.map((x, i) => [x, r[i]]).filter((p) => p[0] > 0), dn = m.map((x, i) => [x, r[i]]).filter((p) => p[0] < 0);
      Object.assign(out, {
        beta, alpha, corr: c, r2: c != null ? c * c : null, te, info: te ? (mean(diff) * 252) / te : null,
        upCapture: up.length ? mean(up.map((p) => p[1])) / mean(up.map((p) => p[0])) : null,
        downCapture: dn.length ? mean(dn.map((p) => p[1])) / mean(dn.map((p) => p[0])) : null,
      });
    }
    return out;
  }

  // Riesgo de una acción contra SPY: beta 5 años mensual (la convención usual), beta 1 año diaria, volatilidad y rendimientos
  function stockRisk(px, spy, rf) {
    if (!px || !spy || !px.t || px.t.length < 60) return null;
    const al = align(px, spy);
    const out = {};
    const ma = monthEnds(al.t, al.a), mb = monthEnds(al.t, al.b);
    const ra = returns(ma), rb = returns(mb);
    if (ra.length >= 24) out.beta5m = cov(ra, rb) / cov(rb, rb);
    const s1 = sliceYears({ t: al.t, c: al.a }, 1), m1 = sliceYears({ t: al.t, c: al.b }, 1);
    const r1 = returns(s1.c), q1 = returns(m1.c);
    if (r1.length > 100 && r1.length === q1.length) out.beta1d = cov(r1, q1) / cov(q1, q1);
    for (const y of [1, 3, 5]) {
      const a = sliceYears({ t: al.t, c: al.a }, y), b = sliceYears({ t: al.t, c: al.b }, y);
      const span = a.t.length ? (Date.parse(a.t[a.t.length - 1]) - Date.parse(a.t[0])) / (365.25 * 864e5) : 0;
      if (span >= y * 0.97) { out["r" + y] = a.c[a.c.length - 1] / a.c[0] - 1; out["spy" + y] = b.c[b.c.length - 1] / b.c[0] - 1; }
    }
    const a3 = sliceYears({ t: al.t, c: al.a }, 3), b3 = sliceYears({ t: al.t, c: al.b }, 3);
    out.s3 = riskStats(returns(a3.c), returns(b3.c), rf);
    out.spyS3 = riskStats(returns(b3.c), returns(b3.c), rf);
    out.maxDD5 = maxDrawdown(al.a);
    const c = px.c, last = c[c.length - 1];
    if (c.length >= 200) out.vsMA200 = last / mean(c.slice(-200)) - 1;
    return out;
  }

  // ---------- fundamentales ----------
  const FIN = /bank|insurance|financial|capital markets|reit|real estate/i;
  function isFinancial(sector) { return FIN.test(String(sector || "")); }

  function fundamentals(sec, price, metric, sector) {
    metric = metric || {};
    const S = (sec && sec.series) || {};
    const out = { years: [], rows: {}, notes: [] };
    if (!S.rev && !S.ni) return null;
    const sh = normSplits(S.sh || {});
    const fy = lastY(S.ni) || lastY(S.rev);
    out.fy = fy;
    const shares = (sec.sharesOut && sec.sharesOut.v) || val(sh, lastY(sh)) || (metric.marketCapitalization && price ? (metric.marketCapitalization * 1e6) / price : null);
    out.shares = shares;
    const mcap = price && shares ? price * shares : (metric.marketCapitalization ? metric.marketCapitalization * 1e6 : null);
    out.mcap = mcap;
    const g = (k) => val(S[k], fy);
    const cash = (g("cash") || 0) + (g("sti") || 0);
    const debt = (g("ltd") || 0) + (g("std") || 0);
    out.cash = cash; out.debt = debt; out.netDebt = debt - cash;
    out.ev = mcap != null ? mcap + debt - cash : null;
    const rev = g("rev"), ni = g("ni"), ebit = g("ebit"), da = g("da"), ocf = g("ocf"), capex = g("capex") || 0;
    const fcf = ocf != null ? ocf - capex : null;
    const ebitda = ebit != null ? ebit + (da || 0) : null;
    Object.assign(out, { rev, ni, ebit, ebitda, fcf, ocf, capex });
    // tasa de impuestos efectiva (0 a 35%), 21% si no hay dato
    const taxR = g("tax") != null && g("pretax") > 0 ? clip(g("tax") / g("pretax"), 0, 0.35) : 0.21;
    const eq = g("eq");
    const invested = (eq || 0) + debt - cash;
    out.roic = ebit != null && invested > 0 ? (ebit * (1 - taxR)) / invested : null;
    out.roe = ni != null && eq > 0 ? ni / eq : null;
    out.roa = ni != null && g("assets") > 0 ? ni / g("assets") : null;
    out.grossM = g("gp") != null && rev ? g("gp") / rev : (g("cogs") != null && rev ? 1 - g("cogs") / rev : null);
    out.opM = ebit != null && rev ? ebit / rev : null;
    out.netM = ni != null && rev ? ni / rev : null;
    out.fcfM = fcf != null && rev ? fcf / rev : null;
    // múltiplos (Finnhub TTM primero; si falta, del último 10-K)
    const pct = (x) => (num(x) != null ? x / 100 : null);
    out.pe = num(metric.peTTM) ?? (mcap && ni > 0 ? mcap / ni : null);
    out.fpe = num(metric.forwardPE);
    out.peg = num(metric.pegTTM);
    out.ps = num(metric.psTTM) ?? (mcap && rev ? mcap / rev : null);
    out.pb = num(metric.pbQuarterly) ?? num(metric.pb) ?? (mcap && eq > 0 ? mcap / eq : null);
    out.evEbitda = num(metric.evEbitdaTTM) ?? (out.ev && ebitda > 0 ? out.ev / ebitda : null);
    out.evSales = num(metric.evRevenueTTM) ?? (out.ev && rev ? out.ev / rev : null);
    out.pfcf = num(metric.pfcfShareTTM) ?? (mcap && fcf > 0 ? mcap / fcf : null);
    out.fcfYield = mcap && fcf != null ? fcf / mcap : (out.pfcf ? 1 / out.pfcf : null);
    out.earnYield = out.ev && ebit != null && out.ev > 0 ? ebit / out.ev : null;
    out.divYield = pct(metric.dividendYieldIndicatedAnnual) ?? (mcap && g("div") ? g("div") / mcap : null);
    out.payout = pct(metric.payoutRatioTTM) ?? (ni > 0 && g("div") ? g("div") / ni : null);
    out.buybackYield = mcap && g("buyback") ? g("buyback") / mcap : null;
    out.shYield = (out.divYield || 0) + (out.buybackYield || 0);
    // deuda y liquidez
    out.de = eq > 0 ? debt / eq : null;
    out.ndEbitda = ebitda > 0 ? out.netDebt / ebitda : null;
    const intY = lastY(S.int), intV = val(S.int, intY);
    out.coverage = ebit != null && intV > 0 && intY >= String(Number(fy) - 1) ? ebit / intV : num(metric.netInterestCoverageTTM);
    out.current = g("ca") != null && g("cl") > 0 ? g("ca") / g("cl") : num(metric.currentRatioQuarterly);
    // crecimiento
    const ry = years(S.rev);
    const growth = (o, n) => { const ys = years(o); if (ys.length < n + 1) return null; return cagr(o[ys[ys.length - 1 - n]], o[ys[ys.length - 1]], n); };
    out.rev3 = growth(S.rev, 3); out.rev5 = growth(S.rev, 5); out.rev10 = growth(S.rev, Math.min(10, ry.length - 1));
    const eps = {}; for (const y of years(S.ni)) if (sh[y] > 0) eps[y] = S.ni[y] / sh[y];
    out.eps5 = growth(eps, 5);
    const fcfS = {}; for (const y of years(S.ocf)) fcfS[y] = S.ocf[y] - ((S.capex || {})[y] || 0);
    out.fcf5 = growth(fcfS, 5);
    const shy = years(sh);
    out.shChg5 = shy.length >= 6 ? sh[shy[shy.length - 1]] / sh[shy[shy.length - 6]] - 1 : null;
    // historia de 10 años para la tabla
    const allY = years(S.rev).length ? years(S.rev) : years(S.ni);
    out.years = allY.slice(-10);
    for (const y of out.years) {
      const r = val(S.rev, y), n = val(S.ni, y), o = val(S.ocf, y), c = val(S.capex, y) || 0, e = val(S.eq, y);
      out.rows[y] = { rev: r, ni: n, fcf: o != null ? o - c : null, netM: r && n != null ? n / r : null, roe: e > 0 && n != null ? n / e : null, sh: val(sh, y) };
    }
    out.fcfSeries = fcfS;
    out.sbc = g("sbc");
    // Múltiplos de los últimos 12 meses con cifras de la SEC (misma fuente y definición que la historia de 10-K)
    const T = sec && sec.ttm;
    if (T && mcap) {
      const tv = (k) => (T[k] && num(T[k].v) != null ? T[k].v : null);
      const tRev = tv("rev"), tNi = tv("ni"), tOcf = tv("ocf"), tCap = tv("capex") || 0, tEbit = tv("ebit"), tDa = tv("da");
      const tDebt = (tv("ltd") ?? g("ltd") ?? 0) + (tv("std") ?? g("std") ?? 0), tCash = (tv("cash") ?? g("cash") ?? 0) + (tv("sti") ?? g("sti") ?? 0);
      const tEv = mcap + tDebt - tCash, tFcf = tOcf != null ? tOcf - tCap : null, tEbitda = tEbit != null ? tEbit + (tDa || 0) : null;
      const sameEnd = (k) => T[k] && T.rev && T[k].end === T.rev.end;
      out.ttmMult = {
        end: T.rev ? T.rev.end : null,
        pe: tNi > 0 && sameEnd("ni") ? mcap / tNi : null,
        ps: tRev > 0 ? mcap / tRev : null,
        evSales: tRev > 0 ? tEv / tRev : null,
        pfcf: tFcf > 0 && sameEnd("ocf") ? mcap / tFcf : null,
        evEbitda: tEbitda > 0 && tEv > 0 && sameEnd("ebit") ? tEv / tEbitda : null,
        pb: tv("eq") > 0 ? mcap / tv("eq") : null,
      };
    }
    out.splitAdjShares = sh;
    out.financial = isFinancial(sector);
    return out;
  }

  // Piotroski F (0-9), con los dos últimos 10-K. Devuelve {score, of, items}
  function piotroski(sec) {
    const S = (sec && sec.series) || {};
    const ys = years(S.ni); if (ys.length < 2) return null;
    const y = ys[ys.length - 1], p = ys[ys.length - 2];
    const v = (k, yy) => val(S[k], yy);
    const sh = normSplits(S.sh || {});
    const items = [];
    const add = (name, ok) => { if (ok != null) items.push({ name, ok: !!ok }); };
    const roa = (yy) => (v("ni", yy) != null && v("assets", yy) > 0 ? v("ni", yy) / v("assets", yy) : null);
    add("Utilidad neta positiva", v("ni", y) != null ? v("ni", y) > 0 : null);
    add("Flujo operativo positivo", v("ocf", y) != null ? v("ocf", y) > 0 : null);
    add("ROA mejora contra el año anterior", roa(y) != null && roa(p) != null ? roa(y) > roa(p) : null);
    add("Flujo operativo mayor que la utilidad (calidad de ganancias)", v("ocf", y) != null && v("ni", y) != null ? v("ocf", y) > v("ni", y) : null);
    const lev = (yy) => (v("assets", yy) > 0 ? (v("ltd", yy) || 0) / v("assets", yy) : null);
    add("Deuda de largo plazo / activos baja", lev(y) != null && lev(p) != null ? lev(y) <= lev(p) : null);
    const cr = (yy) => (v("ca", yy) != null && v("cl", yy) > 0 ? v("ca", yy) / v("cl", yy) : null);
    add("Liquidez corriente mejora", cr(y) != null && cr(p) != null ? cr(y) > cr(p) : null);
    add("No emitió acciones nuevas", sh[y] && sh[p] ? sh[y] <= sh[p] * 1.005 : null);
    const gm = (yy) => (v("gp", yy) != null && v("rev", yy) ? v("gp", yy) / v("rev", yy) : (v("cogs", yy) != null && v("rev", yy) ? 1 - v("cogs", yy) / v("rev", yy) : null));
    add("Margen bruto mejora", gm(y) != null && gm(p) != null ? gm(y) > gm(p) : null);
    const at = (yy) => (v("rev", yy) != null && v("assets", yy) > 0 ? v("rev", yy) / v("assets", yy) : null);
    add("Rotación de activos mejora", at(y) != null && at(p) != null ? at(y) > at(p) : null);
    if (items.length < 6) return null;
    return { score: items.filter((x) => x.ok).length, of: items.length, items, fy: y };
  }

  // Altman Z (versión original, para empresas no financieras)
  function altman(sec, mcap) {
    const S = (sec && sec.series) || {};
    const y = lastY(S.assets); if (!y || !mcap) return null;
    const v = (k) => val(S[k], y);
    const ta = v("assets"), tl = v("liab") ?? (ta != null && v("eq") != null ? ta - v("eq") : null);
    if (!(ta > 0) || !(tl > 0) || v("rev") == null || v("ebit") == null) return null;
    const wc = (v("ca") ?? 0) - (v("cl") ?? 0);
    const z = 1.2 * (wc / ta) + 1.4 * ((v("re") ?? 0) / ta) + 3.3 * (v("ebit") / ta) + 0.6 * (mcap / tl) + 1.0 * (v("rev") / ta);
    return { z, zone: z > 2.99 ? "segura" : z >= 1.81 ? "gris" : "peligro", fy: y };
  }

  // Las 6 reglas del backtest Buffett (PR #7), con la versión más reciente de los 10-K
  function buffett(sec, mcap, dcfVal) {
    const S = (sec && sec.series) || {};
    const niY = years(S.ni).slice(-10);
    const res = [];
    const eq = S.eq || {};
    const yrs = niY.filter((y) => eq[y] > 0);
    const roe = yrs.map((y) => S.ni[y] / eq[y]);
    const roeOk = yrs.length >= 5 && roe.filter((x) => x >= 0.15).length / roe.length >= 0.8 && roe[roe.length - 1] >= 0.15;
    res.push({ name: "ROE de 15% o más en al menos 80% de los años y en el último", ok: yrs.length >= 5 ? roeOk : null,
               detail: yrs.length ? `mediana ${(median(roe) * 100).toFixed(0)}% en ${yrs.length} años` : "sin datos" });
    const last5 = niY.slice(-5).map((y) => S.ni[y]);
    res.push({ name: "Sin pérdidas en los últimos 5 años", ok: last5.length >= 5 ? Math.min(...last5) > 0 : null,
               detail: last5.length ? `peor año ${fmtBig(Math.min(...last5))}` : "sin datos" });
    const lastNi = val(S.ni, lastY(S.ni)), ltdY = lastY(S.ltd), ltd = ltdY ? S.ltd[ltdY] : 0;
    res.push({ name: "Deuda de largo plazo de 3 veces la utilidad anual o menos", ok: lastNi != null ? lastNi > 0 && ltd <= 3 * lastNi : null,
               detail: lastNi > 0 ? `${(ltd / lastNi).toFixed(1)} veces` : "utilidad negativa" });
    const oe = niY.filter((y) => S.ocf && S.ocf[y] != null).map((y) => S.ocf[y] - ((S.capex || {})[y] || 0));
    res.push({ name: "Ganancias del dueño (flujo operativo − inversión) positivas en 80% de los años y en el último", ok: oe.length >= 5 ? oe.filter((x) => x > 0).length / oe.length >= 0.8 && oe[oe.length - 1] > 0 : null,
               detail: oe.length ? `${oe.filter((x) => x > 0).length} de ${oe.length} años` : "sin datos" });
    const sh = normSplits(S.sh || {}), sy = years(sh).slice(-6);
    const chg = sy.length >= 5 ? sh[sy[sy.length - 1]] / sh[sy[0]] - 1 : null;
    res.push({ name: "Acciones en circulación sin crecer más de 5% en 5 años", ok: chg != null ? chg <= 0.05 : null,
               detail: chg != null ? `${chg >= 0 ? "+" : ""}${(chg * 100).toFixed(1)}%` : "sin datos" });
    const mos = dcfVal && mcap ? 1 - mcap / dcfVal : null;
    res.push({ name: "Margen de seguridad: vale en bolsa 75% o menos de su valor intrínseco", ok: mos != null ? mos >= 0.25 : null,
               detail: mos != null ? `margen ${(mos * 100).toFixed(0)}%` : "sin DCF" });
    const known = res.filter((r) => r.ok != null);
    return { items: res, pass: known.filter((r) => r.ok).length, of: known.length, quality: res.slice(0, 5).every((r) => r.ok === true) };
  }

  // Rentabilidad por año (ROE, ROA, ROIC) para ver la tendencia y la mediana de 5 años
  function profitability(sec) {
    const S = (sec && sec.series) || {};
    const out = { years: [], roe: [], roa: [], roic: [] };
    for (const y of years(S.ni).slice(-6)) {
      const g = (k) => val(S[k] || {}, y);
      const ni = g("ni"), eq = g("eq"), as = g("assets"), ebit = g("ebit");
      const debt = (g("ltd") || 0) + (g("std") || 0), cash = (g("cash") || 0) + (g("sti") || 0);
      const taxR = g("tax") != null && g("pretax") > 0 ? clip(g("tax") / g("pretax"), 0, 0.35) : 0.21;
      const inv = (eq || 0) + debt - cash;
      out.years.push(y);
      out.roe.push(ni != null && eq > 0 ? ni / eq : null);
      out.roa.push(ni != null && as > 0 ? ni / as : null);
      out.roic.push(ebit != null && inv > 0 ? (ebit * (1 - taxR)) / inv : null);
    }
    for (const k of ["roe", "roa", "roic"]) { const v = out[k].slice(-5).filter((x) => x != null); out[k + "Med"] = v.length >= 3 ? median(v) : null; }
    return out;
  }

  // ---------- DCF de dos etapas sobre ventas de los últimos 12 meses ----------
  // Flujo libre = ventas × margen de flujo libre (flujo operativo − inversión). Años 1–5 crecen a g; años 6–10 el crecimiento
  // baja en línea recta hasta el perpetuo gt; luego valor terminal de Gordon. Todo se trae a hoy con la tasa r y se resta la deuda neta.
  const ERP = 0.045;   // prima de riesgo del mercado
  function discountRate(rf10, beta) {
    const b = num(beta);
    if (b == null) return { r: 0.10, how: "10% (sin beta)" };
    const adj = 0.67 * b + 0.33;     // beta ajustada (Blume): las betas extremas tienden a regresar a 1
    const raw = (num(rf10) ?? 0.045) + adj * ERP;
    return { r: clip(raw, 0.08, 0.12), raw, adj, how: `bono 10 años ${(100 * (num(rf10) ?? 0.045)).toFixed(1)}% + beta ajustada ${adj.toFixed(2)} × ${(ERP * 100).toFixed(1)}%` };
  }
  function dcfDefaults(sec, f, opts) {
    opts = opts || {};
    const S = (sec && sec.series) || {}, T = (sec && sec.ttm) || null;
    const ys = years(S.ocf).filter((y) => S.rev && S.rev[y] > 0).slice(-3);
    // Flujo libre = flujo operativo − inversión − pagos a empleados en acciones (SBC). El SBC es un gasto real
    // (diluye al accionista) que el flujo operativo suma de vuelta; sin restarlo el DCF infla a las tecnológicas.
    const sbcOf = (y) => num((S.sbc || {})[y]) ?? 0;
    const hasSbc = !!(S.sbc && Object.keys(S.sbc).length);
    const margins = ys.map((y) => (S.ocf[y] - ((S.capex || {})[y] || 0) - sbcOf(y)) / S.rev[y]);
    const marginsGross = ys.map((y) => (S.ocf[y] - ((S.capex || {})[y] || 0)) / S.rev[y]);
    const sbcPct = ys.map((y) => sbcOf(y) / S.rev[y]);
    const marginFrom = ys.slice();
    let rev0 = lastV(S.rev), revFrom = lastY(S.rev) ? `año fiscal ${lastY(S.rev)}` : "";
    const ttm = T && T.rev && T.rev.v > 0 && T.rev.end > (T.rev.fyEnd || "") ? T : null;
    if (ttm) {
      rev0 = ttm.rev.v; revFrom = `últimos 12 meses al ${ttm.rev.end}`;
      if (ttm.ocf && ttm.ocf.end === ttm.rev.end) {
        const sb = hasSbc ? (ttm.sbc && ttm.sbc.end === ttm.rev.end ? ttm.sbc.v : (median(sbcPct) || 0) * ttm.rev.v) : 0;
        const g0 = ttm.ocf.v - ((ttm.capex && ttm.capex.v) || 0);
        margins.push((g0 - sb) / ttm.rev.v); marginsGross.push(g0 / ttm.rev.v); sbcPct.push(sb / ttm.rev.v); marginFrom.push("12m");
      }
    }
    const margin = margins.length ? median(margins) : null;
    const marginNoSbc = marginsGross.length ? median(marginsGross) : null;
    const sbcShare = hasSbc && sbcPct.length ? median(sbcPct) : null;   // SBC como % de las ventas
    const ry = years(S.rev), gr = (n) => (ry.length > n && S.rev[ry[ry.length - 1 - n]] > 0 && S.rev[ry[ry.length - 1]] > 0 ? cagr(S.rev[ry[ry.length - 1 - n]], S.rev[ry[ry.length - 1]], n) : null);
    const g3 = gr(3), g5 = gr(5);
    let hist = [g3, g5].filter((x) => x != null), gYears = hist.length ? null : Math.min(ry.length - 1, 2);
    if (!hist.length && gYears >= 1 && gr(gYears) != null) hist = [gr(gYears)];   // poca historia: usa lo que haya
    const gRaw = hist.length ? mean(hist) : 0;
    // el crecimiento alto no dura: arriba de 6% sólo se toma 40% del exceso (NVDA 83% → 25% tope; INOD 41% → 20%)
    const gAdj = gRaw <= 0.06 ? gRaw : 0.06 + 0.4 * (gRaw - 0.06);
    const dr = discountRate(opts.rf10, opts.beta);
    // deuda neta con el balance más reciente (10-Q) cuando está al día
    let netDebt = f ? f.netDebt : 0, ndFrom = "último 10-K";
    if (ttm && f) {
      const fresh = (k) => (ttm[k] && Math.abs(Date.parse(ttm[k].end) - Date.parse(ttm.end)) < 100 * 864e5 ? ttm[k].v : null);
      const pick = (k) => fresh(k) ?? val(S[k] || {}, f.fy) ?? 0;   // un saldo viejo (de hace años) no cuenta
      if (["cash", "ltd", "std", "sti"].some((k) => fresh(k) != null)) {
        netDebt = pick("ltd") + pick("std") - pick("cash") - pick("sti"); ndFrom = `balance al ${ttm.end}`;
      }
    }
    return { rev0, revFrom, margin, marginNoSbc, sbcShare, hasSbc, marginFrom, g: clip(gAdj, 0, 0.25), gRaw, g3, g5, gYears, r: dr.r, rHow: dr.how, rRaw: dr.raw,
             gt: 0.03, years: 10, stage1: 5, netDebt, ndFrom, shares: f ? f.shares : null };
  }
  // Método conservador del backtest Buffett (PR #7): flujo libre promedio de 3 años, crecimiento de ventas 0–8%, 10%, 3%.
  // Se queda como referencia y para la regla 6 de Buffett, que es la que se probó en el backtest.
  function dcfConservative(sec, f) {
    const S = (sec && sec.series) || {};
    const ys = years(S.ocf).slice(-3);
    const oe = ys.map((y) => S.ocf[y] - ((S.capex || {})[y] || 0));
    const ry = years(S.rev).slice(-6);
    let g = 0;
    if (ry.length >= 4 && S.rev[ry[0]] > 0 && S.rev[ry[ry.length - 1]] > 0) g = cagr(S.rev[ry[0]], S.rev[ry[ry.length - 1]], ry.length - 1);
    return { base: oe.length ? mean(oe) : null, baseYears: ys, g: clip(g || 0, 0, 0.08), gRaw: g, r: 0.10, gt: 0.03, years: 10,
             netDebt: f ? f.netDebt : 0, shares: f ? f.shares : null };
  }
  function dcfValue(p) {
    const base = p.rev0 != null && p.margin != null ? p.rev0 * p.margin : p.base;
    if (!(base > 0) || !(p.r > p.gt)) return null;
    const n = p.years || 10, n1 = p.stage1 != null ? Math.min(p.stage1, n) : n;
    let pv = 0, cf = base, rev = p.rev0 != null ? p.rev0 : null;
    const rows = [];
    for (let t = 1; t <= n; t++) {
      const g = t <= n1 ? p.g : p.g + ((p.gt - p.g) * (t - n1)) / (n - n1);
      cf *= 1 + g; if (rev != null) rev *= 1 + g;
      const d = cf / Math.pow(1 + p.r, t);
      pv += d; rows.push({ t, g, rev, fcf: cf, pv: d });
    }
    const tv = (cf * (1 + p.gt)) / (p.r - p.gt) / Math.pow(1 + p.r, n);
    const equity = pv + tv - (p.netDebt || 0);
    return { pv, tv, equity, base, rows, perShare: p.shares ? equity / p.shares : null, tvShare: pv + tv ? tv / (pv + tv) : null };
  }
  // Tres escenarios alrededor de los supuestos actuales
  function dcfScenarios(p) {
    const m = (k) => (p.margin != null ? { margin: p.margin * k } : { base: p.base * k });
    return {
      bear: dcfValue({ ...p, ...m(0.85), g: p.g * 0.5, r: p.r + 0.01 }),
      base: dcfValue(p),
      bull: dcfValue({ ...p, ...m(1.1), g: Math.min(p.g * 1.5, p.g + 0.15), r: Math.max(p.gt + 0.02, p.r - 0.01) }),
    };
  }
  function dcfGrid(p, price) {
    const rs = [-0.02, -0.01, 0, 0.01, 0.02].map((d) => p.r + d), gs = [-0.1, -0.05, 0, 0.05, 0.1].map((d) => p.g + d);
    return { rs, gs, cells: rs.map((r) => gs.map((g) => { const v = dcfValue({ ...p, r, g }); return v && v.perShare ? { v: v.perShare, mos: price ? 1 - price / v.perShare : null } : null; })) };
  }
  // DCF al revés: qué crecimiento (etapa 1) justifica el precio de hoy, con todo lo demás igual
  function impliedGrowth(p, price) {
    if (!p.shares || !price) return null;
    const target = price * p.shares;
    const f = (g) => { const v = dcfValue({ ...p, g }); return v ? v.equity - target : null; };
    let lo = -0.3, hi = 0.8, flo = f(lo), fhi = f(hi);
    if (flo == null || fhi == null || flo > 0 || fhi < 0) return flo != null && flo > 0 ? { g: lo, below: true } : fhi != null ? { g: hi, above: true } : null;
    for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2, fm = f(mid); if (fm > 0) hi = mid; else lo = mid; }
    return { g: (lo + hi) / 2 };
  }
  const capm = (rf, beta, erp = 0.05) => (num(rf) != null && num(beta) != null ? rf + beta * erp : null);

  // ---------- portafolio ----------
  // ETFs comunes: clase de activo y región para las recomendaciones
  const ETF = {
    broad: "SPY VOO IVV VTI ITOT SCHB SPLG RSP SCHX VV IWB SPTM".split(" "),
    nasdaq: "QQQ QQQM ONEQ".split(" "),
    smallmid: "IWM VB VO IJH IJR MDY SCHA VTWO".split(" "),
    factor: "SCHD VIG DGRO VYM HDV NOBL MOAT QUAL USMV SPLV MTUM VLUE VTV VUG IWF IWD SPYG SPYV DVY SDY COWZ".split(" "),
    intl: "VXUS VEA VWO IEFA IEMG EFA EEM ACWX IXUS SPDW SCHF VEU EWW INDA FXI EWJ EWZ MCHI EWG EWU EWC KWEB VGK".split(" "),
    global: "VT ACWI URTH".split(" "),
    bonds: "BND AGG TLT IEF SHY BIL SGOV VGSH VGIT VGLT BNDX TIP SCHP LQD HYG JNK MUB VCIT VCSH GOVT IEI SCHZ SCHO SCHR BSV BIV BLV USFR TFLO JPST MINT".split(" "),
    gold: "GLD IAU SLV GLDM SGOL PDBC DBC".split(" "),
    reit: "VNQ SCHH XLRE IYR".split(" "),
    sector: "XLK XLF XLE XLV XLY XLP XLI XLU XLB XLC VGT SMH SOXX ARKK IBB XBI KRE ITB IGV CIBR SKYY HACK TAN ICLN JETS".split(" "),
    crypto: "IBIT FBTC GBTC ETHA BITO ARKB BITB".split(" "),
    leveraged: "TQQQ SQQQ SOXL SOXS UPRO SPXL SPXS TNA TZA LABU NVDL TSLL UVXY".split(" "),
  };
  const ETF_LABEL = { broad: "Índice EE. UU. amplio", nasdaq: "Nasdaq 100", smallmid: "Empresas medianas y pequeñas", factor: "Factor / dividendos EE. UU.",
    intl: "Internacional", global: "Global", bonds: "Bonos / Tesoro", gold: "Oro y materias primas", reit: "Bienes raíces (REIT)", sector: "ETF de sector",
    crypto: "Cripto", leveraged: "Apalancado / inverso" };
  function etfClass(sym) { for (const [k, list] of Object.entries(ETF)) if (list.includes(sym)) return k; return null; }
  const MEGA = "AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA BRK.B BRK-B JPM LLY".split(" ");

  // Sector amplio a partir de la industria de Finnhub
  const SECTOR_MAP = [
    [/semiconductor|technology|software|computer|electronic|communications equipment|it services/i, "Tecnología"],
    [/bank|insurance|financial|capital markets|diversified financ/i, "Finanzas"],
    [/pharma|biotech|health|medical|life sciences/i, "Salud"],
    [/media|entertainment|telecom|interactive|communication/i, "Comunicación"],
    [/retail|auto|hotel|restaurant|leisure|textile|apparel|consumer discretionary|household durables|distributors/i, "Consumo discrecional"],
    [/beverage|food|tobacco|household products|personal products|consumer staples/i, "Consumo básico"],
    [/oil|gas|energy|coal/i, "Energía"],
    [/utilit/i, "Servicios públicos"],
    [/real estate|reit/i, "Bienes raíces"],
    [/chemical|metal|mining|paper|forest|construction materials|packaging|steel/i, "Materiales"],
    [/aerospace|defense|machinery|industrial|airline|logistics|transport|building|electrical equipment|construction|commercial services|professional services|road|rail|marine/i, "Industria"],
  ];
  function broadSector(industry) { for (const [re, name] of SECTOR_MAP) if (re.test(String(industry || ""))) return name; return industry || "Otro"; }

  function assetBucket(sym, prof) {
    const e = etfClass(sym);
    if (e) return { kind: "etf", cls: e, label: ETF_LABEL[e], sector: ETF_LABEL[e] };
    if (prof && prof.etf) return { kind: "etf", cls: "other", label: "ETF / fondo", sector: "ETF / fondo" };
    const intl = prof && prof.country && prof.country !== "US";
    return { kind: "stock", cls: intl ? "intlstock" : "usstock", label: intl ? "Acción extranjera" : "Acción EE. UU.", sector: broadSector(prof && prof.sector) };
  }

  const PROFILES = {
    conservador: { label: "Conservador", beta: [0.2, 0.6], maxStock: 0.05, minBonds: 0.4, maxEquity: 0.5 },
    moderado_conservador: { label: "Moderado conservador", beta: [0.4, 0.8], maxStock: 0.07, minBonds: 0.25, maxEquity: 0.7 },
    moderado: { label: "Moderado", beta: [0.6, 1.0], maxStock: 0.10, minBonds: 0.1, maxEquity: 0.85 },
    crecimiento: { label: "Crecimiento", beta: [0.8, 1.15], maxStock: 0.12, minBonds: 0, maxEquity: 1 },
    agresivo: { label: "Agresivo", beta: [0.9, 1.35], maxStock: 0.15, minBonds: 0, maxEquity: 1 },
  };

  // Rendimientos diarios del portafolio con los pesos de hoy (como si lo hubieras tenido todo el periodo).
  // Si una acción todavía no cotizaba un día, ese día se reparte entre las que sí.
  function portfolioSeries(series, weights) {
    const spy = series.SPY; if (!spy || spy.t.length < 60) return null;
    const syms = Object.keys(weights).filter((s) => weights[s] > 0 && series[s] && series[s].t.length > 1);
    const maps = Object.fromEntries(syms.map((s) => [s, new Map(series[s].t.map((d, i) => [d, series[s].c[i]]))]));
    const t = [], r = [], m = [];
    const cashW = weights.__cash || 0;
    for (let i = 1; i < spy.t.length; i++) {
      const d0 = spy.t[i - 1], d1 = spy.t[i];
      let wsum = 0, acc = 0;
      for (const s of syms) {
        const a = maps[s].get(d0), b = maps[s].get(d1);
        if (a > 0 && b > 0) { wsum += weights[s]; acc += weights[s] * (b / a - 1); }
      }
      if (wsum <= 0) continue;
      const inv = 1 - cashW;
      t.push(d1); r.push((acc / wsum) * inv); m.push(spy.c[i] / spy.c[i - 1] - 1);
    }
    return { t, r, m, syms };
  }

  // Aporte de cada posición al riesgo total: w_i * (Σw)_i / σ²
  function riskContrib(series, weights, syms) {
    const spy = series.SPY;
    const R = {};
    for (const s of syms) {
      const mp = new Map(series[s].t.map((d, i) => [d, series[s].c[i]]));
      R[s] = [];
      for (let i = 1; i < spy.t.length; i++) { const a = mp.get(spy.t[i - 1]), b = mp.get(spy.t[i]); R[s].push(a > 0 && b > 0 ? b / a - 1 : 0); }
    }
    const n = syms.length, C = syms.map((a) => syms.map((b) => cov(R[a], R[b]) || 0));
    const w = syms.map((s) => weights[s] || 0), Cw = C.map((row) => row.reduce((acc, x, j) => acc + x * w[j], 0));
    const varP = w.reduce((acc, x, i) => acc + x * Cw[i], 0);
    const contrib = {}; syms.forEach((s, i) => { contrib[s] = varP > 0 ? (w[i] * Cw[i]) / varP : null; });
    const corrM = syms.map((a) => syms.map((b) => (a === b ? 1 : corr(R[a], R[b]))));
    let pairs = 0, sum = 0; for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) if (corrM[i][j] != null) { pairs++; sum += corrM[i][j]; }
    return { contrib, corr: corrM, avgCorr: pairs ? sum / pairs : null };
  }

  // Recomendaciones educativas con reglas claras. level: "alerta" | "considera" | "bien"
  function recommendations(ctx) {
    const { holdings, total, cash, stats, spyStats, profile, risk } = ctx;
    const P = PROFILES[profile] || PROFILES.moderado;
    const out = [];
    const add = (level, title, text) => out.push({ level, title, text });
    if (!holdings.length) return out;
    const port = total + cash;
    const w = (h) => h.value / port;
    const stocks = holdings.filter((h) => h.bucket.kind === "stock");
    const etfs = holdings.filter((h) => h.bucket.kind === "etf");
    const pctS = (x) => `${(x * 100).toFixed(0)}%`;

    for (const h of stocks.filter((h) => w(h) > P.maxStock).sort((a, b) => w(b) - w(a)))
      add("alerta", `${h.symbol} pesa ${pctS(w(h))} del portafolio`, `Para un perfil ${P.label.toLowerCase()} una sola acción no debería pasar de ~${pctS(P.maxStock)}. Si a esa empresa le va mal, arrastra todo el portafolio.`);
    const top3 = [...stocks].sort((a, b) => b.value - a.value).slice(0, 3).reduce((s, h) => s + w(h), 0);
    if (stocks.length >= 3 && top3 > 0.5) add("alerta", `Tus 3 acciones más grandes son ${pctS(top3)}`, "Más de la mitad del dinero depende de tres empresas. Repartir entre más posiciones o un ETF amplio baja ese riesgo.");
    const bySector = {};
    for (const h of holdings) if (h.bucket.kind === "stock" || h.bucket.cls === "sector") bySector[h.bucket.sector] = (bySector[h.bucket.sector] || 0) + w(h);
    for (const [s, x] of Object.entries(bySector)) if (x > 0.35) add("alerta", `${pctS(x)} está en ${s}`, "Un solo sector arriba de 35% hace que el portafolio suba y baje con ese sector. El S&P 500 tiene el más grande cerca de 30%.");
    const broadW = etfs.filter((h) => ["broad", "global", "factor", "smallmid"].includes(h.bucket.cls)).reduce((s, h) => s + w(h), 0);
    if (stocks.length && stocks.length < 10 && broadW < 0.3) add("considera", `Sólo ${stocks.length} acciones y poco índice`, "Con menos de 10 acciones individuales la suerte de cada empresa pesa mucho. Lo común es tener un núcleo en un ETF amplio (60% o más) y las acciones como complemento.");
    const intl = holdings.filter((h) => h.bucket.cls === "intl" || h.bucket.cls === "global" || h.bucket.cls === "intlstock").reduce((s, h) => s + w(h), 0);
    if (intl < 0.05) add("considera", "Todo está en Estados Unidos", "No tienes casi nada fuera de EE. UU. Muchos portafolios diversificados ponen 20 a 40% en el resto del mundo (por ejemplo un ETF internacional amplio).");
    const bonds = holdings.filter((h) => h.bucket.cls === "bonds").reduce((s, h) => s + w(h), 0) + cash / port;
    if (bonds < P.minBonds) add("alerta", `Renta fija y efectivo: ${pctS(bonds)}`, `Para un perfil ${P.label.toLowerCase()} lo usual es al menos ${pctS(P.minBonds)} en bonos, CETES o efectivo, para aguantar caídas sin vender.`);
    if (stats && stats.beta != null) {
      if (stats.beta > P.beta[1]) add("alerta", `Beta ${stats.beta.toFixed(2)}: más riesgo que tu perfil`, `Tu perfil apunta a una beta entre ${P.beta[0]} y ${P.beta[1]}. Con ${stats.beta.toFixed(2)}, si el S&P cae 10%, tu portafolio tiende a caer ~${(stats.beta * 10).toFixed(0)}%.`);
      else if (stats.beta < P.beta[0]) add("considera", `Beta ${stats.beta.toFixed(2)}: menos riesgo que tu perfil`, `Tu perfil acepta una beta de ${P.beta[0]} a ${P.beta[1]}. Está bien si es a propósito; si no, puede que estés dejando rendimiento esperado.`);
      else add("bien", `Beta ${stats.beta.toFixed(2)} acorde a tu perfil`, `Está dentro del rango ${P.beta[0]}–${P.beta[1]} de un perfil ${P.label.toLowerCase()}.`);
    }
    if (risk && risk.avgCorr != null && holdings.length >= 3) {
      if (risk.avgCorr > 0.6) add("considera", `Tus posiciones se mueven juntas (correlación ${risk.avgCorr.toFixed(2)})`, "Cuando casi todo sube y baja al mismo tiempo, tener varias posiciones no diversifica mucho. Mezclar sectores, regiones o bonos ayuda.");
      else if (risk.avgCorr < 0.4) add("bien", `Buena diversificación (correlación ${risk.avgCorr.toFixed(2)})`, "Tus posiciones no se mueven todas igual, lo que suaviza las caídas.");
    }
    if (risk && risk.contrib) for (const h of holdings) {
      const c = risk.contrib[h.symbol];
      if (c != null && c > 0.25 && c > 1.6 * w(h)) add("considera", `${h.symbol} es ${pctS(w(h))} del dinero pero ${pctS(c)} del riesgo`, "Esta posición mueve tu portafolio mucho más que su tamaño. Vale la pena revisar si ese riesgo es intencional.");
    }
    if (stats && spyStats && stats.sharpe != null && spyStats.sharpe != null) {
      if (stats.sharpe < spyStats.sharpe - 0.15) add("considera", `Sharpe ${stats.sharpe.toFixed(2)} contra ${spyStats.sharpe.toFixed(2)} del SPY`, "Con los pesos de hoy, en este periodo el portafolio dio menos rendimiento por unidad de riesgo que simplemente tener el S&P 500. Es el punto de comparación más honesto.");
      else if (stats.sharpe > spyStats.sharpe + 0.15) add("bien", `Sharpe ${stats.sharpe.toFixed(2)} arriba del SPY (${spyStats.sharpe.toFixed(2)})`, "En este periodo dio más rendimiento por riesgo que el índice. Ojo: el pasado no garantiza que se repita.");
    }
    if (stats && spyStats && stats.maxDD < spyStats.maxDD - 0.1) add("considera", `Peor caída ${pctS(stats.maxDD)} contra ${pctS(spyStats.maxDD)} del SPY`, "Pregúntate si aguantarías esa caída sin vender. Si no, conviene un portafolio más defensivo.");
    const core = etfs.filter((h) => ["broad", "nasdaq"].includes(h.bucket.cls));
    const overl = stocks.filter((h) => MEGA.includes(h.symbol));
    if (core.length && overl.length) add("considera", `Ya tienes ${overl.map((h) => h.symbol).join(", ")} dentro de ${core.map((h) => h.symbol).join(", ")}`, "Esas empresas ya son una parte grande del índice. Comprarlas aparte aumenta tu exposición a ellas más de lo que parece.");
    const lev = holdings.filter((h) => h.bucket.cls === "leveraged");
    if (lev.length) add("alerta", `ETF apalancado: ${lev.map((h) => h.symbol).join(", ")}`, "Los ETF apalancados o inversos se rebalancean cada día y pierden valor con el tiempo cuando el mercado va de lado. Son para plazos muy cortos.");
    const cry = holdings.filter((h) => h.bucket.cls === "crypto").reduce((s, h) => s + w(h), 0);
    if (cry > 0.05) add("considera", `Cripto ${pctS(cry)}`, "Arriba de 5% en cripto la volatilidad del portafolio sube mucho. Muchos asesores lo limitan a 1–5%.");
    if (cash / port > 0.25 && (profile === "crecimiento" || profile === "agresivo")) add("considera", `Efectivo ${pctS(cash / port)}`, "Para un perfil de crecimiento es mucho efectivo; con el tiempo la inflación se lo come. Si es un colchón a propósito, está bien.");
    for (const h of holdings) if (h.cost && h.price && h.price / h.cost - 1 < -0.3) add("considera", `${h.symbol} va ${pctS(h.price / h.cost - 1)} desde tu compra`, "Revisa si la razón por la que la compraste sigue en pie. Lo que importa es lo que vale hoy, no lo que pagaste.");
    if (!out.some((x) => x.level === "alerta")) add("bien", "Sin alertas graves", "No hay concentraciones fuertes ni un riesgo fuera de tu perfil con las reglas de este tablero.");
    const order = { alerta: 0, considera: 1, bien: 2 };
    return out.sort((a, b) => order[a.level] - order[b.level]);
  }

  function fmtBig(x) {
    if (x == null || !isFinite(x)) return "—";
    const a = Math.abs(x), s = x < 0 ? "-" : "";
    if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)} B`;   // billones (español)
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(1)} mil M`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)} M`;
    return `${s}$${a.toFixed(0)}`;
  }

  // ---------- múltiplos en la historia ----------
  // Para cada año fiscal: valor en bolsa al cierre del año (precio ajustado por splits) × acciones diluidas de ese año.
  // Los precios de Alpaca también vienen ajustados por dividendos; se deshace ese ajuste con los dividendos pagados (aprox.).
  function multiplesHistory(sec, px) {
    const S = (sec && sec.series) || {};
    if (!px || !px.t || !px.t.length || !(S.rev || S.ni)) return null;
    const sh = normSplits(S.sh || {});
    // Precio al cierre del AÑO FISCAL de cada 10-K (NVDA cierra en enero, AAPL en septiembre), no al 31 de diciembre
    const endPx = (y) => { const e = fiscalEnd(sec, y); if (!e || px.t[0] > e) return null; let v = null; for (let i = 0; i < px.t.length; i++) { if (px.t[i] <= e) v = px.c[i]; else break; } return v; };
    const ys = years(sh).filter((y) => endPx(y) != null);
    const out = { years: [], pe: {}, ps: {}, pb: {}, pfcf: {}, evEbitda: {}, peg: {}, evSales: {} };
    if (!ys.length) return out;
    let F = 1;
    const mc = {};
    for (let i = ys.length - 1; i >= 0; i--) {
      const y = ys[i];
      if (i < ys.length - 1) { const n = ys[i + 1], d = val(S.div, n); if (d > 0 && mc[n] > 0) F *= 1 + d / mc[n]; }
      mc[y] = endPx(y) * F * sh[y];
    }
    const eps = {}; for (const y of years(S.ni)) if (sh[y] > 0) eps[y] = S.ni[y] / sh[y];
    for (const y of ys) {
      const m = mc[y], ni = val(S.ni, y), rev = val(S.rev, y), eq = val(S.eq, y);
      const ocf = val(S.ocf, y), fcf = ocf != null ? ocf - (val(S.capex, y) || 0) : null;
      const ebit = val(S.ebit, y), ebitda = ebit != null ? ebit + (val(S.da, y) || 0) : null;
      const ev = m + (val(S.ltd, y) || 0) + (val(S.std, y) || 0) - (val(S.cash, y) || 0) - (val(S.sti, y) || 0);
      out.years.push(y);
      if (ni > 0) out.pe[y] = m / ni;
      if (rev > 0) { out.ps[y] = m / rev; out.evSales[y] = ev / rev; }
      if (eq > 0) out.pb[y] = m / eq;
      if (fcf > 0) out.pfcf[y] = m / fcf;
      if (ebitda > 0 && ev > 0) out.evEbitda[y] = ev / ebitda;
      const y3 = String(Number(y) - 3), g = cagr(eps[y3], eps[y], 3);
      if (out.pe[y] && g > 0) out.peg[y] = out.pe[y] / (g * 100);
    }
    return out;
  }
  // Fecha de cierre del año fiscal y (misma etiqueta que la función: año de la fecha de cierre menos 180 días).
  // 1) la que manda la función; 2) el mes y día del último cierre conocido; 3) 31 de diciembre.
  function fiscalEnd(sec, y) {
    const E = sec && sec.fyEnds;
    if (E && E[y]) return E[y];
    const known = (E && lastV2(E)) || (sec && sec.ttm && sec.ttm.rev && sec.ttm.rev.fyEnd) || null;
    if (!known) return `${y}-12-31`;
    const md = known.slice(5, 10);
    for (const yy of [Number(y), Number(y) + 1]) {
      const d = `${yy}-${md}`, t = Date.parse(d + "T00:00:00Z") - 180 * 864e5;
      if (String(new Date(t).getUTCFullYear()) === String(y)) return d;
    }
    return `${y}-12-31`;
  }
  function lastV2(o) { const y = lastY(o); return y ? o[y] : null; }

  // Resumen de un múltiplo: actual contra la mediana y el rango de su propia historia
  function multipleStat(hist, cur) {
    const v = Object.values(hist || {}).filter((x) => num(x) != null && x > 0);
    if (v.length < 3 || !(cur > 0)) return { n: v.length, cur: cur > 0 ? cur : null };
    const med = median(v), lo = Math.min(...v), hi = Math.max(...v), ratio = cur / med;
    return { n: v.length, cur, med, lo, hi, ratio, verdict: ratio < 0.85 ? "bajo" : ratio > 1.15 ? "alto" : "normal" };
  }

  const QX = { multiplesHistory, multipleStat, fiscalEnd, num, years, lastY, lastV, mean, median, std, cov, corr, cagr, clip, normSplits, returns, maxDrawdown, align, monthEnds, sliceYears,
    riskStats, stockRisk, fundamentals, piotroski, altman, buffett, profitability, dcfDefaults, dcfConservative, dcfValue, dcfScenarios, dcfGrid, impliedGrowth, discountRate, capm, isFinancial,
    etfClass, ETF_LABEL, broadSector, assetBucket, PROFILES, portfolioSeries, riskContrib, recommendations, fmtBig };
  if (typeof module !== "undefined" && module.exports) module.exports = QX; else root.QX = QX;
})(typeof self !== "undefined" ? self : this);
