// QX Fundamental: perfil del inversionista, portafolios recomendados y simulaciones.
// Funciones puras (sin red ni pantalla); las usan la página y las pruebas (node tests/test_fundamental.js).
// Educativo: los portafolios son modelos de referencia, no recomendaciones personalizadas de un asesor registrado.
(function (root) {
  "use strict";

  // ---------------- cuestionario ----------------
  // kind: "one" (una opción) | "many" (varias) | "num" (número). Cada opción: [texto, puntos].
  // dim: "know" conocimiento · "cap" capacidad de asumir riesgo · "will" disposición · "pref" preferencias (no puntúa)
  const SECTIONS = [
    { id: "know", title: "Conocimiento y experiencia", intro: "Para saber qué tan complejo puede ser tu portafolio y cuánto explicarte.", qs: [
      { id: "k1", dim: "know", kind: "one", q: "¿Cuánto sabes de inversiones?", opts: [
        ["Nada o casi nada", 0], ["Lo básico: sé qué es una acción, un bono y un ETF", 1],
        ["Intermedio: entiendo diversificación, P/U, tasas de interés y riesgo", 2], ["Avanzado: analizo estados financieros, valuación o derivados", 3]] },
      { id: "k2", dim: "know", kind: "one", q: "¿Cuánto tiempo llevas invirtiendo en bolsa?", opts: [
        ["Nunca he invertido", 0], ["Menos de 1 año", 1], ["De 1 a 3 años", 2], ["De 3 a 10 años", 3], ["Más de 10 años", 3]] },
      { id: "k3", dim: "know", kind: "many", q: "¿En qué has invertido alguna vez? (marca todas las que apliquen)", opts: [
        ["CETES, pagarés o depósitos a plazo", 0.5], ["Fondos de inversión o ETFs", 1], ["Acciones individuales", 1],
        ["Opciones, futuros o apalancamiento", 1], ["Criptomonedas", 0.5], ["Bienes raíces o FIBRAs", 0.5]] },
      { id: "k4", dim: "know", kind: "one", q: "Si las tasas de interés suben, ¿qué pasa normalmente con el precio de los bonos que ya existen?", opts: [
        ["Sube", 0], ["Baja", 3], ["No cambia", 0], ["No sé", 0]] },
      { id: "k5", dim: "know", kind: "one", q: "¿Qué es más riesgoso, en general?", opts: [
        ["Tener todo en una sola acción", 3], ["Tener todo en un ETF del S&P 500", 0], ["Es igual", 0], ["No sé", 0]] },
      { id: "k6", dim: "know", kind: "one", q: "Un fondo rindió 30% el año pasado. ¿Qué te dice eso de este año?", opts: [
        ["Que probablemente vuelva a rendir mucho", 0], ["Casi nada: el rendimiento pasado no garantiza el futuro", 3], ["Que ya no va a subir", 0], ["No sé", 0]] },
    ] },
    { id: "goals", title: "Objetivos y horizonte", intro: "El plazo es lo que más define cuánto riesgo puedes tomar.", qs: [
      { id: "o1", dim: "pref", kind: "one", q: "¿Cuál es el objetivo principal de este dinero?", opts: [
        ["Cuidar lo que tengo (que no pierda valor)", 0], ["Recibir ingresos periódicos", 1], ["Crecer de forma balanceada", 2],
        ["Crecer lo más posible a largo plazo", 3], ["Una meta concreta (casa, retiro, estudios)", 2]] },
      { id: "o2", dim: "cap", kind: "one", q: "¿Cuándo vas a necesitar la mayor parte de este dinero?", opts: [
        ["En menos de 1 año", 0], ["En 1 a 3 años", 1], ["En 3 a 5 años", 2], ["En 5 a 10 años", 3], ["En más de 10 años", 4]], w: 2 },
      { id: "o3", dim: "cap", kind: "one", q: "¿Qué edad tienes?", opts: [
        ["Menos de 30", 4], ["30 a 44", 3], ["45 a 59", 2], ["60 a 69", 1], ["70 o más", 0]] },
      { id: "o4", dim: "cap", kind: "one", q: "¿Necesitarás sacar dinero de esta inversión de forma regular?", opts: [
        ["Sí, cada mes", 0], ["De vez en cuando", 2], ["No, la dejo crecer", 4]] },
    ] },
    { id: "fin", title: "Situación financiera", intro: "Tu capacidad de aguantar una caída sin tener que vender.", qs: [
      { id: "f1", dim: "cap", kind: "one", q: "¿Qué tan estables son tus ingresos?", opts: [
        ["Muy inestables o no tengo ingresos fijos", 0], ["Variables (comisiones, negocio propio)", 1], ["Estables (sueldo fijo)", 3], ["Muy estables y con otras fuentes de ingreso", 4]] },
      { id: "f2", dim: "cap", kind: "one", q: "¿Tienes un fondo de emergencia aparte de esta inversión?", opts: [
        ["No tengo", 0], ["Menos de 3 meses de gastos", 1], ["De 3 a 6 meses de gastos", 3], ["Más de 6 meses de gastos", 4]] },
      { id: "f3", dim: "cap", kind: "one", q: "¿Qué parte de tu patrimonio (sin contar tu casa) es este dinero?", opts: [
        ["Más de 75%", 0], ["De 50 a 75%", 1], ["De 25 a 50%", 3], ["Menos de 25%", 4]] },
      { id: "f4", dim: "cap", kind: "one", q: "¿Tienes deudas caras, como tarjetas de crédito que no pagas completas?", opts: [
        ["Sí, bastantes", 0], ["Algunas", 2], ["No", 4]] },
      { id: "f5", dim: "cap", kind: "one", q: "¿Cuántas personas dependen económicamente de ti?", opts: [
        ["Tres o más", 1], ["Una o dos", 2], ["Ninguna", 4]] },
      { id: "f6", dim: "pref", kind: "num", q: "¿Cuánto piensas invertir al inicio? (dólares)", ph: "10000" },
      { id: "f7", dim: "pref", kind: "num", q: "¿Cuánto podrías agregar cada mes? (dólares)", ph: "200" },
    ] },
    { id: "risk", title: "Tolerancia al riesgo", intro: "Cómo reaccionarías de verdad. No hay respuestas correctas.", qs: [
      { id: "t1", dim: "will", kind: "one", q: "Inviertes $10,000 y en 3 meses valen $7,500 (−25%) porque todo el mercado cayó. ¿Qué haces?", opts: [
        ["Vendo todo para no perder más", 0], ["Vendo una parte", 1], ["Espero sin hacer nada", 3], ["Compro más porque está barato", 4]], w: 2 },
      { id: "t2", dim: "will", kind: "one", q: "¿Qué rango de resultados en un año malo y uno bueno prefieres?", opts: [
        ["Entre −2% y +6%", 0], ["Entre −8% y +12%", 1], ["Entre −18% y +25%", 3], ["Entre −35% y +45%", 4]], w: 2 },
      { id: "t3", dim: "will", kind: "one", q: "¿Cuál es la mayor caída que aguantarías sin vender?", opts: [
        ["5%", 0], ["10 a 15%", 1], ["20 a 30%", 3], ["40% o más", 4]] },
      { id: "t4", dim: "will", kind: "one", q: "¿Qué frase te describe mejor?", opts: [
        ["Evito perder dinero a toda costa", 0], ["Acepto un poco de riesgo por algo más de rendimiento", 1],
        ["Acepto riesgo moderado por un rendimiento claramente mayor", 3], ["Acepto mucho riesgo por el mayor rendimiento posible", 4]] },
      { id: "t5", dim: "will", kind: "one", q: "¿Qué te molestaría más?", opts: [
        ["Perder 20% en un año", 0], ["Las dos cosas por igual", 2], ["Ganar 5% cuando el mercado ganó 25%", 4]] },
      { id: "t6", dim: "will", kind: "one", q: "En las caídas de 2020 o 2022, si tenías dinero invertido, ¿qué hiciste?", opts: [
        ["No tenía dinero invertido", 2], ["Vendí", 0], ["No hice nada", 3], ["Compré más", 4]] },
    ] },
    { id: "pref", title: "Preferencias", intro: "Para armar los portafolios a tu gusto.", qs: [
      { id: "p1", dim: "pref", kind: "one", q: "¿Cómo te gustaría manejar tu dinero?", opts: [
        ["Todo automático con pocos ETFs", 0], ["ETFs y algunas acciones que yo elija", 1], ["Me gusta analizar y elegir acciones", 2]] },
      { id: "p2", dim: "pref", kind: "one", q: "¿Dónde quieres invertir?", opts: [
        ["Sólo en Estados Unidos", 0], ["Sobre todo en Estados Unidos", 1], ["Diversificado en todo el mundo", 2]] },
      { id: "p3", dim: "pref", kind: "one", q: "¿Prefieres dividendos o crecimiento?", opts: [
        ["Prefiero recibir dividendos", 0], ["Me da igual", 1], ["Prefiero crecimiento (aunque no pague dividendos)", 2]] },
      { id: "p4", dim: "pref", kind: "one", q: "¿Quieres algo de México en tu portafolio?", opts: [["No", 0], ["Sí, una parte pequeña", 1]] },
      { id: "p5", dim: "pref", kind: "one", q: "¿Te interesa tener oro como protección?", opts: [["Sí", 1], ["No", 0]] },
      { id: "p6", dim: "pref", kind: "one", q: "¿En qué moneda vas a gastar este dinero?", opts: [["Pesos", 0], ["Dólares", 1], ["En las dos", 2]] },
      { id: "p7", dim: "pref", kind: "one", q: "¿Te importa invertir con criterios ambientales y sociales (ESG)?", opts: [["Sí", 1], ["Me da igual", 0]] },
      { id: "p8", dim: "pref", kind: "one", q: "¿Te interesa tener algo en criptomonedas?", opts: [["No", 0], ["Sí, una parte muy pequeña", 1]] },
    ] },
  ];
  const QUESTIONS = SECTIONS.flatMap((s) => s.qs);

  const PROFILE_ORDER = ["conservador", "moderado_conservador", "moderado", "crecimiento", "agresivo"];
  const PROFILE_TEXT = {
    conservador: "Prioridad: no perder. Mayoría en bonos de corto plazo y CETES; algo de acciones para no perder contra la inflación.",
    moderado_conservador: "Crecimiento moderado con caídas acotadas. Más renta fija que acciones.",
    moderado: "Balance entre crecer y aguantar caídas. Alrededor de 60% acciones y 40% renta fija.",
    crecimiento: "Busca crecer a largo plazo y acepta caídas fuertes en el camino.",
    agresivo: "Casi todo en acciones. Puede caer 40–50% en una crisis; sólo tiene sentido con plazo largo y nervios firmes.",
  };

  function scoreOne(q, a) {
    if (a == null) return null;
    if (q.kind === "many") return (Array.isArray(a) ? a : []).reduce((s, i) => s + (q.opts[i] ? q.opts[i][1] : 0), 0);
    if (q.kind === "one") return q.opts[a] ? q.opts[a][1] : null;
    return null;
  }
  function missing(answers) {
    return QUESTIONS.filter((q) => q.kind !== "num" && q.kind !== "many" && (answers[q.id] == null || answers[q.id] === "")).map((q) => q.id);
  }

  // Calificación: capacidad (puede) y disposición (quiere), de 0 a 1. Manda la más baja, como en la práctica profesional.
  function evaluate(answers) {
    const acc = { cap: [0, 0], will: [0, 0], know: [0, 0] };
    for (const q of QUESTIONS) {
      if (!acc[q.dim]) continue;
      const w = q.w || 1, s = scoreOne(q, answers[q.id]);
      const max = q.kind === "many" ? 3 : Math.max(...q.opts.map((o) => o[1]));
      if (s == null) continue;
      acc[q.dim][0] += w * Math.min(s, max); acc[q.dim][1] += w * max;
    }
    const r = (d) => (acc[d][1] ? acc[d][0] / acc[d][1] : 0.5);
    const cap = r("cap"), will = r("will"), know = r("know");
    let score = Math.min(cap, will) * 0.75 + Math.max(cap, will) * 0.25;
    const cuts = [0.3, 0.45, 0.6, 0.76];
    let idx = cuts.filter((c) => score >= c).length;
    const caps = [], notes = [];
    const hz = answers.o2;
    if (hz === 0) { idx = 0; caps.push("Necesitas el dinero en menos de un año: con ese plazo una caída no da tiempo de recuperarse."); }
    else if (hz === 1 && idx > 1) { idx = 1; caps.push("Con un plazo de 1 a 3 años el perfil se limita a moderado conservador."); }
    else if (hz === 2 && idx > 2) { idx = 2; caps.push("Con un plazo de 3 a 5 años el perfil se limita a moderado."); }
    if (answers.o4 === 0 && idx > 2) { idx = 2; caps.push("Como vas a retirar dinero cada mes, el perfil se limita a moderado."); }
    if (answers.f2 === 0 && idx > 2) { idx = 2; caps.push("Sin fondo de emergencia el perfil se limita a moderado: una urgencia te obligaría a vender en mal momento."); }
    const level = know < 0.3 ? 0 : know < 0.55 ? 1 : know < 0.8 ? 2 : 3;
    if (answers.f2 === 0 || answers.f2 === 1) notes.push("Antes de invertir más, junta de 3 a 6 meses de gastos en algo líquido y seguro (CETES a 28 días o cuenta de ahorro).");
    if (answers.f4 === 0 || answers.f4 === 1) notes.push("Pagar tarjetas que cobran 40–60% al año rinde más que cualquier inversión. Conviene hacerlo primero.");
    if (Math.abs(cap - will) > 0.3) notes.push(cap < will
      ? "Tu disposición al riesgo es mayor que tu capacidad (plazo, ingresos, fondo de emergencia). El perfil sigue a la capacidad."
      : "Tu situación permite más riesgo del que te sientes cómodo tomando. El perfil respeta tu comodidad: un portafolio que no aguantas termina vendiéndose en el peor momento.");
    if (level <= 1) notes.push("Como vas empezando, los portafolios usan pocos ETFs amplios. Las acciones individuales quedan para cuando tengas más experiencia.");
    if (answers.p6 === 0) notes.push("Vas a gastar en pesos: si inviertes en dólares, el tipo de cambio sube o baja tu resultado. La parte de efectivo conviene tenerla en CETES.");
    if (answers.p8 === 1) notes.push(idx >= 2 && level >= 1
      ? "Cripto: si quieres, limítala a 1–5% del total y cuéntala dentro de tu parte de acciones. No entra en los portafolios modelo porque su historia es corta y muy volátil."
      : "Cripto: con tu perfil o experiencia actual conviene dejarla fuera; puede caer 70% o más en un año.");
    const profile = PROFILE_ORDER[idx];
    return { profile, idx, score, cap, will, know, level, caps, notes, text: PROFILE_TEXT[profile],
             amount: Number(answers.f6) > 0 ? Number(answers.f6) : 10000, monthly: Number(answers.f7) >= 0 && answers.f7 !== "" && answers.f7 != null ? Number(answers.f7) : 0 };
  }

  // Perfil elegido a mano: toma el resultado del cuestionario (si hay) y cambia sólo el perfil.
  // Sin cuestionario usa supuestos generales (experiencia básica, $10,000 sin aportes).
  function withProfile(ev, profile) {
    const idx = PROFILE_ORDER.indexOf(profile);
    if (idx < 0) return ev || null;
    if (ev) return { ...ev, profile, idx, text: PROFILE_TEXT[profile], quizProfile: ev.profile, quizIdx: ev.idx, manual: profile !== ev.profile };
    return { profile, idx, score: null, cap: null, will: null, know: null, level: 1, caps: [], notes: [], text: PROFILE_TEXT[profile],
             amount: 10000, monthly: 0, quizProfile: null, quizIdx: null, manual: true, noQuiz: true };
  }

  // ---------------- portafolios ----------------
  // Mezcla por perfil: acciones / bonos / efectivo / oro
  const MIX = {
    conservador: { eq: 0.25, bond: 0.50, cash: 0.20, gold: 0.05 },
    moderado_conservador: { eq: 0.40, bond: 0.42, cash: 0.13, gold: 0.05 },
    moderado: { eq: 0.60, bond: 0.30, cash: 0.05, gold: 0.05 },
    crecimiento: { eq: 0.80, bond: 0.15, cash: 0.00, gold: 0.05 },
    agresivo: { eq: 0.95, bond: 0.05, cash: 0.00, gold: 0.00 },
  };
  // Clase de activo de cada ETF que usan los modelos (para la simulación a futuro)
  const ASSET = {
    VTI: "us", VOO: "us", ESGV: "us", SCHD: "usdiv", QUAL: "usq", MOAT: "usq", JQUA: "usq", USMV: "uslv", VB: "ussmall",
    VXUS: "intl", VEA: "intl", VSGX: "intl", VWO: "em", EWW: "mx",
    BND: "bond", IEF: "bond", TIP: "tips", BIL: "cash", GLD: "gold",
  };
  const NAMES = {
    VTI: "Todo el mercado de EE. UU.", VOO: "S&P 500", ESGV: "EE. UU. con filtro ESG", SCHD: "Dividendos de EE. UU.", QUAL: "Calidad EE. UU.", MOAT: "Ventaja competitiva (Morningstar)",
    JQUA: "Calidad y rentabilidad EE. UU. (JPMorgan)", USMV: "Mínima volatilidad EE. UU. (iShares)",
    VB: "Empresas pequeñas EE. UU.", VXUS: "Todo el mundo fuera de EE. UU.", VEA: "Países desarrollados fuera de EE. UU.", VSGX: "Mundo fuera de EE. UU. con ESG",
    VWO: "Mercados emergentes", EWW: "México", BND: "Bonos de EE. UU. (todo el mercado)", IEF: "Bonos del Tesoro 7–10 años", TIP: "Bonos ligados a inflación",
    BIL: "T-bills 1–3 meses (equivalente a CETES en dólares)", GLD: "Oro",
  };
  const KIND = { us: "eq", usdiv: "eq", usq: "eq", uslv: "eq", ussmall: "eq", intl: "eq", em: "eq", mx: "eq", bond: "bond", tips: "bond", cash: "cash", gold: "gold" };

  function round(w) {
    const out = {}; let tot = 0;
    for (const [k, v] of Object.entries(w)) if (v > 0.004) { out[k] = Math.round(v * 100) / 100; tot += out[k]; }
    const big = Object.keys(out).sort((a, b) => out[b] - out[a])[0];
    if (big) out[big] = Math.round((out[big] + 1 - tot) * 100) / 100;
    return out;
  }

  function profileMix(ev, answers) {
    const m = { ...MIX[ev.profile] };
    if (answers && answers.p5 === 1 && m.gold) { m.bond += m.gold; m.gold = 0; }   // sin oro: va a bonos
    return m;
  }
  // Perfiles que buscan menos sustos: la mitad de las acciones de EE. UU. va a mínima volatilidad (USMV)
  const CAUTIOUS = { conservador: 1, moderado_conservador: 1, moderado: 1 };

  // Referencia justa: la misma mezcla del perfil hecha sólo con índices baratos (S&P 500, bonos y T-bills).
  // Es contra lo que hay que medir cada portafolio; el S&P 500 solo es la referencia sólo del perfil agresivo.
  function referenceWeights(ev, answers) {
    const m = profileMix(ev, answers);
    return round({ VOO: m.eq, BND: m.bond, BIL: m.cash + m.gold });
  }

  function buildPortfolios(ev, answers) {
    const m = profileMix(ev, answers);
    const lowVol = CAUTIOUS[ev.profile] ? 0.5 : 0;
    const intlShare = answers.p2 === 0 ? 0.1 : answers.p2 === 1 ? 0.25 : 0.4;
    const esg = answers.p7 === 0;
    const US = esg ? "ESGV" : "VTI", INTL = esg ? "VSGX" : "VXUS";
    const div = answers.p3 === 0, mx = answers.p4 === 1;
    const out = [];

    // 1. Sencillo: 3 o 4 fondos
    {
      const w = {};
      const eqUS = m.eq * (1 - intlShare), eqIn = m.eq * intlShare;
      const core = eqUS * (1 - lowVol);
      if (div) { w[US] = core * 0.65; w.SCHD = core * 0.35; } else w[US] = core;
      if (lowVol) w.USMV = eqUS * lowVol;
      w[INTL] = eqIn * (mx ? 0.8 : 1); if (mx) w.EWW = eqIn * 0.2;
      w.BND = m.bond; w.BIL = m.cash; w.GLD = m.gold;
      out.push({ id: "simple", name: "Sencillo", tag: "pocos fondos, barato y fácil de mantener",
        why: "Tres o cuatro ETFs amplios cubren miles de empresas y bonos. Es la base que recomiendan la mayoría de los estudios: costo bajo y casi nada que vigilar." +
          (lowVol ? " La mitad de las acciones de EE. UU. va a USMV (mínima volatilidad): en la historia ha tenido caídas más chicas con un rendimiento por riesgo parecido; en mercados muy alcistas rinde menos." : ""), w: round(w) });
    }
    // 2. Diversificado: más piezas (emergentes, bonos ligados a inflación). Sin VB: las pequeñas sin filtro no tienen premio comprobado.
    {
      const w = {};
      const eqUS = m.eq * (1 - intlShare), eqIn = m.eq * intlShare;
      const core = eqUS * (1 - lowVol);
      w.VOO = core * (div ? 0.7 : 1); if (div) w.SCHD = core * 0.3; if (lowVol) w.USMV = eqUS * lowVol;
      w.VEA = eqIn * (mx ? 0.6 : 0.7); w.VWO = eqIn * 0.3; if (mx) w.EWW = eqIn * 0.1;
      w.BND = m.bond * 0.5; w.IEF = m.bond * 0.2; w.TIP = m.bond * 0.3; w.BIL = m.cash; w.GLD = m.gold;
      out.push({ id: "div", name: "Diversificado", tag: "más regiones y tipos de bono",
        why: "Agrega países desarrollados y emergentes por separado y bonos que protegen contra inflación. No garantiza más rendimiento, pero reparte mejor los riesgos." +
          (lowVol ? " Igual que en el Sencillo, la mitad de las acciones de EE. UU. va a mínima volatilidad (USMV)." : ""), w: round(w) });
    }
    // 3. Núcleo + satélite de calidad (Buffett), si el conocimiento y el perfil lo permiten
    {
      const sat = ev.level >= 2 && ev.idx >= 2 ? (answers.p1 === 2 ? 0.2 : 0.15) : ev.level >= 1 && ev.idx >= 1 ? 0.1 : 0;
      if (sat > 0) {
        const base = out[0].w, w = {};
        const eqTot = Object.entries(base).filter(([k]) => KIND[ASSET[k]] === "eq").reduce((s, [, v]) => s + v, 0);
        for (const [k, v] of Object.entries(base)) w[k] = KIND[ASSET[k]] === "eq" ? v * (1 - sat / eqTot) : v;
        w.JQUA = (w.JQUA || 0) + sat * 0.5; w.USMV = (w.USMV || 0) + sat * 0.5;
        out.push({ id: "sat", name: "Núcleo + satélite de factores", tag: `${Math.round(sat * 100)}% en rentabilidad y baja volatilidad`,
          why: `El núcleo es el portafolio Sencillo. El satélite (${Math.round(sat * 100)}%) va a empresas rentables y de calidad (JQUA) y de baja volatilidad (USMV): son las dos inclinaciones que siguieron dando algo después de publicarse en los estudios (1963–2026). Ojo: sin ventaja comprobada; en 30 fondos reales ninguno le ganó al S&P 500 con significancia. Por eso es un satélite chico, no el núcleo.`, w: round(w), sat });
      } else {
        const w = { ...out[0].w };
        const fromEq = Object.entries(w).filter(([k]) => KIND[ASSET[k]] === "eq");
        const cut = 0.1; let taken = 0;
        for (const [k, v] of fromEq) { const d = v * cut; w[k] = v - d; taken += d; }
        w.BIL = (w.BIL || 0) + taken;
        out.push({ id: "safe", name: "Sencillo más defensivo", tag: "10% menos acciones",
          why: "Igual al Sencillo pero con más efectivo, por si quieres empezar con menos sustos. Puedes subir la parte de acciones cuando te sientas cómodo.", w: round(w) });
      }
    }
    for (const p of out) {
      p.mix = { eq: 0, bond: 0, cash: 0, gold: 0 };
      for (const [k, v] of Object.entries(p.w)) p.mix[KIND[ASSET[k]]] += v;
    }
    return out;
  }

  // ---------------- simulaciones ----------------
  // Precios diarios -> rendimientos mensuales (último cierre de cada mes)
  function monthly(series) {
    const t = series.t, c = series.c, out = { m: [], r: [] };
    let prev = null, prevM = null;
    for (let i = 0; i < t.length; i++) {
      const ym = t[i].slice(0, 7), last = i === t.length - 1 || t[i + 1].slice(0, 7) !== ym;
      if (!last) continue;
      if (prev != null) { out.m.push(ym); out.r.push(c[i] / prev - 1); }
      prev = c[i]; prevM = ym;
    }
    return out;
  }

  // Historia real: rebalanceo mensual a los pesos objetivo. Si un ETF todavía no existía, su peso se reparte entre los demás.
  function backtest(seriesMap, weights, opts) {
    const init = opts.initial || 10000, add = opts.monthly || 0;
    const M = {};
    for (const s of Object.keys(weights)) if (seriesMap[s] && seriesMap[s].t.length > 30) M[s] = monthly(seriesMap[s]);
    const spy = seriesMap.SPY ? monthly(seriesMap.SPY) : null;
    const months = [...new Set(Object.values(M).flatMap((x) => x.m))].sort();
    const idx = Object.fromEntries(Object.entries(M).map(([s, x]) => [s, new Map(x.m.map((m, i) => [m, x.r[i]]))]));
    const spyIdx = spy ? new Map(spy.m.map((m, i) => [m, spy.r[i]])) : null;
    let v = init, vs = init, contrib = init, peak = init, mdd = 0, unit = 1, unitPeak = 1;
    const path = [], ret = [], years = {};
    for (const m of months) {
      let ws = 0, acc = 0;
      for (const [s, w] of Object.entries(weights)) { const r = idx[s] && idx[s].get(m); if (r != null) { ws += w; acc += w * r; } }
      if (ws < 0.5) continue;              // menos de la mitad del portafolio existía: no cuenta
      const r = acc / ws, rs = spyIdx ? spyIdx.get(m) ?? 0 : 0;
      v = v * (1 + r) + add; vs = vs * (1 + rs) + add; contrib += add;
      unit *= 1 + r; unitPeak = Math.max(unitPeak, unit); mdd = Math.min(mdd, unit / unitPeak - 1);
      ret.push(r); path.push({ m, v, vs, contrib });
      const y = m.slice(0, 4); years[y] = (years[y] ?? 1) * (1 + r);
      peak = Math.max(peak, v);
    }
    if (ret.length < 12) return null;
    const n = ret.length, mean = ret.reduce((s, x) => s + x, 0) / n;
    const sd = Math.sqrt(ret.reduce((s, x) => s + (x - mean) ** 2, 0) / (n - 1));
    const cagr = Math.pow(unit, 12 / n) - 1;
    const yr = Object.entries(years).map(([y, g]) => ({ y, r: g - 1 }));
    return { path, months: n, start: path[0].m, end: path[path.length - 1].m, final: v, finalSpy: vs, contributed: contrib,
             cagr, vol: sd * Math.sqrt(12), maxDD: mdd, worstYear: yr.reduce((a, b) => (b.r < a.r ? b : a), yr[0]), bestYear: yr.reduce((a, b) => (b.r > a.r ? b : a), yr[0]), years: yr };
  }

  // Supuestos a futuro (rendimiento anual nominal en dólares). Más bajos que los últimos 10 años a propósito:
  // en 2016-2026 las acciones de EE. UU. rindieron muy por arriba de su promedio de largo plazo.
  const EXPECTED = { us: 0.065, usdiv: 0.065, usq: 0.065, uslv: 0.06, ussmall: 0.07, intl: 0.07, em: 0.075, mx: 0.075, bond: 0.043, tips: 0.042, cash: null, gold: 0.04 };
  const VOL_FALLBACK = { us: 0.16, usdiv: 0.15, usq: 0.15, uslv: 0.12, ussmall: 0.21, intl: 0.16, em: 0.2, mx: 0.24, bond: 0.06, tips: 0.06, cash: 0.005, gold: 0.15 };

  function mulberry(seed) { return function () { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function gauss(rnd) { let u = 0, v = 0; while (u === 0) u = rnd(); while (v === 0) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
  function cholesky(A) {
    const n = A.length, L = A.map(() => new Array(n).fill(0));
    for (let i = 0; i < n; i++) for (let j = 0; j <= i; j++) {
      let s = A[i][j]; for (let k = 0; k < j; k++) s -= L[i][k] * L[j][k];
      L[i][j] = i === j ? Math.sqrt(Math.max(s, 1e-12)) : s / L[j][j];
    }
    return L;
  }

  // Monte Carlo: rendimientos mensuales con las volatilidades y correlaciones reales de cada ETF (desde 2016)
  // y los rendimientos esperados de EXPECTED. Devuelve percentiles por año y la probabilidad de terminar abajo de lo aportado.
  function monteCarlo(seriesMap, weights, opts) {
    const years = opts.years || 10, init = opts.initial || 10000, add = opts.monthly || 0, N = opts.paths || 2000, rf = opts.rf ?? 0.04;
    const syms = Object.keys(weights).filter((s) => weights[s] > 0);
    const M = {}; for (const s of syms) if (seriesMap[s]) M[s] = monthly(seriesMap[s]);
    const common = syms.filter((s) => M[s] && M[s].r.length >= 24);
    const months = common.length ? [...new Set(common.flatMap((s) => M[s].m))].sort() : [];
    const mp = Object.fromEntries(common.map((s) => [s, new Map(M[s].m.map((m, i) => [m, M[s].r[i]]))]));
    const n = syms.length;
    const sd = syms.map((s) => {
      const r = M[s] ? M[s].r : [];
      if (r.length < 24) return VOL_FALLBACK[ASSET[s]] / Math.sqrt(12);
      const mu = r.reduce((a, b) => a + b, 0) / r.length;
      return Math.sqrt(r.reduce((a, b) => a + (b - mu) ** 2, 0) / (r.length - 1));
    });
    const C = syms.map((a, i) => syms.map((b, j) => {
      if (i === j) return 1;
      if (!mp[a] || !mp[b]) return 0.3;
      const xs = [], ys = []; for (const m of months) { const x = mp[a].get(m), y = mp[b].get(m); if (x != null && y != null) { xs.push(x); ys.push(y); } }
      if (xs.length < 24) return 0.3;
      const mx = xs.reduce((s, x) => s + x, 0) / xs.length, my = ys.reduce((s, x) => s + x, 0) / ys.length;
      let sxy = 0, sxx = 0, syy = 0; for (let k = 0; k < xs.length; k++) { sxy += (xs[k] - mx) * (ys[k] - my); sxx += (xs[k] - mx) ** 2; syy += (ys[k] - my) ** 2; }
      return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0;
    }));
    const L = cholesky(C);
    // media mensual logarítmica para que el rendimiento anual esperado sea EXPECTED
    const mu = syms.map((s, i) => { const a = ASSET[s], e = a === "cash" ? rf : EXPECTED[a] ?? 0.06; return Math.log(1 + e) / 12 - (sd[i] ** 2) / 2; });
    const w = syms.map((s) => weights[s]);
    const rnd = mulberry(opts.seed || 42);
    const T = years * 12, finals = [], byYear = Array.from({ length: years + 1 }, () => []);
    let below = 0, lostHalf = 0;
    const contributed = init + add * T;
    for (let p = 0; p < N; p++) {
      let v = init, peak = 1, unit = 1, worst = 0;
      byYear[0].push(v);
      for (let t = 1; t <= T; t++) {
        const z = syms.map(() => gauss(rnd));
        let r = 0;
        for (let i = 0; i < n; i++) { let e = 0; for (let k = 0; k <= i; k++) e += L[i][k] * z[k]; r += w[i] * (Math.exp(mu[i] + sd[i] * e) - 1); }
        v = v * (1 + r) + add; unit *= 1 + r; peak = Math.max(peak, unit); worst = Math.min(worst, unit / peak - 1);
        if (t % 12 === 0) byYear[t / 12].push(v);
      }
      finals.push(v); if (v < contributed) below++; if (worst <= -0.3) lostHalf++;
    }
    const pct = (arr, q) => { const b = [...arr].sort((x, y) => x - y); return b[Math.min(b.length - 1, Math.floor(q * b.length))]; };
    const bands = byYear.map((a, y) => ({ y, p10: pct(a, 0.1), p50: pct(a, 0.5), p90: pct(a, 0.9) }));
    const expRet = syms.reduce((s, sym, i) => s + w[i] * (ASSET[sym] === "cash" ? rf : EXPECTED[ASSET[sym]] ?? 0.06), 0);
    return { years, contributed, bands, p10: pct(finals, 0.1), p50: pct(finals, 0.5), p90: pct(finals, 0.9),
             probBelow: below / N, probDD30: lostHalf / N, expRet, paths: N };
  }

  // Pesos actuales por clase de activo contra la mezcla del portafolio recomendado
  function mixOf(holdings, cash, assetBucket) {
    const tot = holdings.reduce((s, h) => s + h.value, 0) + cash;
    const mix = { eq: 0, bond: 0, cash: cash / (tot || 1), gold: 0, other: 0 };
    for (const h of holdings) {
      const b = h.bucket || assetBucket(h.symbol, h.prof);
      const k = b.kind === "stock" ? "eq" : ({ bonds: "bond", gold: "gold", crypto: "other", leveraged: "other" })[b.cls] || "eq";
      mix[k] += h.value / (tot || 1);
    }
    return mix;
  }

  const QXP = { SECTIONS, QUESTIONS, PROFILE_ORDER, PROFILE_TEXT, MIX, ASSET, NAMES, KIND, EXPECTED, evaluate, withProfile, missing, buildPortfolios, referenceWeights, monthly, backtest, monteCarlo, mixOf };
  if (typeof module !== "undefined" && module.exports) module.exports = QXP; else root.QXP = QXP;
})(typeof self !== "undefined" ? self : this);
