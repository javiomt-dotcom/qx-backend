---
name: qx-trading-terminal
description: Operar y modificar el proyecto QX de Javier (terminal de trading, motor cuantitativo, back-end qx-backend con Alpaca, Supabase, Telegram y app de iPhone).
---

# QX: terminal de trading y motor cuantitativo

Usa esta skill cuando Javier pida algo del proyecto QX: la terminal, la lista diaria de compras, el swing de 5 días, el bot de Telegram, la app de iPhone, la base de datos, los workflows de GitHub o la investigación del modelo.

**Idioma:** Javier escribe en español. Responde siempre en español, en frases completas y sencillas. Esto no es asesoría financiera y los mensajes lo dicen.

## 1. Objetivo

Un tablero tipo terminal financiera con precios en vivo y un motor de decisión 100% cuantitativo que cada día estudia las acciones que más subieron, detecta patrones del día anterior y ordena qué comprar en la siguiente sesión. Hoy el producto real es la **estrategia swing de 5 días**; la lista de 1 día sigue corriendo pero perdió contra SPY.

## 2. Piezas y dónde viven

| Pieza | Dónde | Qué hace |
|---|---|---|
| Terminal web (artifact) | https://claude.ai/artifact/VnotK8JdmcSS4UDnETCoFb, fuente `/mnt/project-files/terminal/terminal.html` | Terminal en claude.ai con datos de IBKR (conector "Interactive Brokers (IBKR)"), motor en el navegador, pestañas incluida "Servidor" que lee Supabase |
| Back-end | repo `github.com/javiomt-dotcom/qx-backend` (rama `main`), copia en `/mnt/project-files/terminal/backend/` | Rutina diaria, alertas, bot y research en GitHub Actions |
| Base de datos | Supabase (proyecto `jswqsaogomxyjtyhoucn`), REST con llave service_role, RLS activo | Velas, listas, resultados, noticias, posiciones |
| Mensajes | Bot de Telegram, sólo contesta al chat de Javier | Lista del día, resultados vs SPY, alertas, noticias, comandos |
| App iPhone (PWA) | `docs/` del repo, servida en https://javiomt-dotcom.github.io/qx-backend/ | Login Supabase (correo + contraseña), pestañas Hoy, Posiciones, Mis posiciones, Resultados, Noticias |
| Investigación | `research.py`, `news_research.py`; informes en `/mnt/project-files/terminal/research/` | Backtests walk-forward de 3 años |

## 3. Back-end (qx-backend)

Estructura:
- `daily.py`: rutina completa después del cierre (`--local` usa SQLite, `--force` rehace la lista, `--check` revisa secretos y conexiones).
- `intraday.py`: alertas durante la sesión.
- `bot.py`: comandos de Telegram por long-polling.
- `weekly.py`: reporte semanal del swing y del modelo de 1 día contra SPY (`--dry` sólo imprime, `--force` lo repite).
- `qx/engine.py`: modelo de 1 día (logística calibrada), backtest, reglas de entrada.
- `qx/swing.py`: estrategia swing de 5 días (versión L de la investigación).
- `qx/earnings.py`: calendario de resultados (Alpha Vantage EARNINGS_CALENDAR).
- `qx/news.py`: titulares Alpaca/Benzinga con léxico de tono propio.
- `qx/data_alpaca.py`, `qx/store.py` (Supabase y SQLite con la misma interfaz), `qx/notify.py`, `qx/sp500.py`, `qx/config.py` (todos los parámetros).
- `schema.sql`: tablas `symbols, bars, forecasts, picks, outcomes, daily_results, runs, swing_picks, swing_results, earnings, alerts, news_daily, news_market`.
- Fuera de `schema.sql` (creado en Supabase por MCP): RPC `public.qx_app()` (SECURITY DEFINER), tabla `qx_viewers` y función `qx_allowed()` (acceso por correos aprobados), tabla `user_positions` (RLS por usuario) y Edge Function `quotes` (precios IEX de Alpaca y aperturas para la app).

Workflows de GitHub Actions:

| Workflow | Archivo | Horario (UTC) |
|---|---|---|
| QX diario | `daily.yml` → `daily.py` | 21:30 y 22:30 lun-vie (la segunda es respaldo; no hace nada si la lista ya existe). Entradas `force` y `check` |
| QX alertas | `intraday.yml` → `intraday.py` | 3 trabajos largos por día (13:20, 15:50, 18:20) que revisan cada 15 min |
| QX bot | `bot.yml` → `bot.py` | Ventanas largas 13, 16, 19, 22 y 01 UTC (≈7am a 10pm México) |
| QX semanal | `weekly.yml` → `weekly.py` | Viernes 23:45 y respaldo sábado 15:00. Se marca en `runs` (kind `weekly`) para no repetir la semana. Entradas `force` y `dry` |
| QX investigación | `research.yml` → `research.py` | Manual |
| QX noticias (investigación) | `news.yml` → `news_research.py` | Manual, modos probe / research / today / save |

GitHub se salta muchas corridas programadas frecuentes; por eso alertas y bot usan pocos trabajos largos con bucles adentro.

### Secretos (sólo nombres, nunca valores)

Los workflows aceptan los nombres de Javier o los estándar: `ALPACA_KEY` (o `ALPACA_KEY_ID`), `ALPACA_SECRETO` (o `ALPACA_SECRET_KEY`), `BOT` (o `TELEGRAM_BOT_TOKEN`), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_CHAT_ID`, `ALPHAVANTAGE_KEY`. Variables opcionales: `QX_CAPITAL` (10000), `QX_RISK` (0.01), `QX_MAXPOS` (0.20). La Edge Function `quotes` usa los secretos de Supabase `ALPACA_KEY` y `ALPACA_SECRETO`. Nunca pidas ni pegues llaves en el chat o el código.

## 4. Flujo diario

1. Baja velas diarias de Alpaca: S&P 500 más acciones líquidas (precio ≥ $5 y ≥ $5M diarios promedio 20d), ~3 años de historia.
2. Guarda en Supabase.
3. Califica la lista anterior con reglas realistas y la compara contra SPY.
4. Reentrena (incluye sus propios resultados reales con peso ×5) y guarda la lista para la siguiente sesión.
5. Corre el swing de 5 días con filtro de resultados.
6. Arma la sección de noticias.
7. Manda todo por Telegram (swing primero). Si algo falla, manda alerta de fallo.

## 5. Modelos y reglas

**Modelo de 1 día** (`qx/engine.py`, igual que la terminal): 12 rasgos de la víspera (retorno del día, volumen relativo, cierre en el rango, expansión de rango, momentum 5d, distancia a máximo 20d, RSI 14, volatilidad 20d, gap, vs media 20d, vs media 50d, vs SPY), z-score por día recortado a ±3, regresión logística que predice "estar entre los que más suben mañana", walk-forward de 60 sesiones reentrenando cada 20, vida media 120 sesiones, recalibración tipo Platt. 10 picks, máximo 3 fuera del S&P, descarta saltos de más de 40% en el día.

**Entrada realista del modelo de 1 día:** compra en la apertura siguiente; si abre más de 1 ATR arriba del cierre no se compra (columna "No comprar arriba de"); stop 1.5 ATR y objetivo 3 ATR desde la entrada (si tocó ambos, gana el stop); si no, vende al cierre. Tamaño por riesgo con filtro de régimen del S&P (100 / 50 / 25%).

**Swing de 5 días** (`qx/swing.py`, el que se usa): cada día compra 10 acciones en la apertura con 1/5 del capital y vende al cierre de la quinta sesión. **No usa stop ni objetivo**: `plan()` sólo calcula el límite "No comprar arriba de" (cierre + 1 ATR) y las acciones (`QX_CAPITAL / 5 / 10` por posición, $200 con el capital default de $10,000, así que una acción de más de $200 queda en 0 acciones). La caída de 2 ATR es sólo una alerta, no una venta. Puntaje = modelo ridge de retorno a 5 días ajustado por volatilidad + tendencia (z de distancia a la media de 50d). Filtros: ATR ≤ 8% del precio, sin saltos > 40%, máximo 3 fuera del S&P, sin acciones que reporten resultados durante los 5 días.

**Calificación real del swing** (`score_session()`): promedia las acciones de cada lista; si una abrió arriba de su "No comprar arriba de" el día de compra, no se compró y cuenta como efectivo (0%) los 5 días (desde 2026-11-02; los resultados guardados antes no se recalcularon).

**Alertas intradía:** acción de la lista que abre arriba de "No comprar arriba de", posición que cae más de 2 ATR desde la compra, y en la última hora qué lista se vende al cierre. Cada aviso una sola vez al día.

**Noticias:** sólo contexto, no entran al modelo. Sección "📰 Noticias del día": cierre del SPY, reacción a noticias buenas y malas, titulares macro sin acción, acciones que más se movieron con noticia, temas en aumento (5 sesiones contra 20), atención inusual.

## 6. Resultados de investigación (no volver a discutirlos sin datos nuevos)

- 3 años walk-forward (informe `2026-10-22-modelo.md`): 1 día abrir-cerrar pierde (−56%); swing 5 días mezcla modelo + tendencia +106% (Sharpe 1.36, caída −25%) contra SPY +34%. Cautelas: sesgo de supervivencia, ruido de ±45 puntos entre corridas, 2026 pesa mucho.
- Real 22 y 23 de octubre: el modelo de 1 día perdió contra SPY.
- Noticias (informe `2026-10-28-noticias.md`): IC casi cero; swing + noticias +109.7% vs +109.1% sin ellas (ruido). Filtrar noticias negativas empeora. Decisión: noticias sólo como contexto.
- Las acciones pequeñas del screener (< $5) perdieron mucho en real (CANG −30%, CMCT −28%); de ahí el tope de 3 fuera del S&P y el precio mínimo de $5.

## 7. Comandos del bot

`/lista` (lista swing más reciente), `/posiciones` (listas abiertas, cuánto van y cuándo se venden), `/resultados` (acumulado real contra SPY), `/noticias` (noticias del último cierre), `/semana` (reporte de la semana contra SPY), `/ayuda`. Sólo contesta al `TELEGRAM_CHAT_ID`.

## 8. Terminal (artifact)

- Datos del conector IBKR desde la página: límite duro de 30 llamadas por minuto compartido; todo pasa por una cola con prioridad (~2.2 s entre llamadas). Historia acumulada en IndexedDB.
- Top 20 "Todo el mercado" usa el screener guardado de IBKR "QX Ganadores" y respeta su orden; filas < $5 llevan una etiqueta.
- Panel principal "Swing 5 días · lista oficial": lee `swing_picks` y `swing_results` de Supabase (la misma lista de Telegram y la app) con la etiqueta "experimento en papel". Sólo si Supabase no responde usa el swing calculado en la página, y lo dice.
- El modelo de 1 día vive en la pestaña "Modelo 1 día · Patrones · Backtest", marcado "sólo comparación"; su botón "Lista 1 día" (antes "Compras de mañana", sólo con la bolsa cerrada) abre y guarda su lista. Botón principal: "Analizar y guardar el día".
- Pestaña Servidor: paneles de Supabase (listas, swing, noticias).
- Para cambiarla: edita `/mnt/project-files/terminal/terminal.html` y vuelve a publicar en la misma URL.

## 9. App de iPhone

PWA en `docs/` (index.html, sw.js, manifest, `vendor/supabase.js` local). Entra con correo y contraseña; los datos llegan por la RPC `qx_app()` y sólo para correos en `qx_viewers` (agrega correos sólo cuando Javier los nombre). Precios en vivo cada 15 s con el mercado abierto vía la Edge Function `quotes`. "Mis posiciones" guarda compras por usuario en `user_positions`.

## 10. Cómo hacer cambios

1. Trabaja en el repo `qx-backend`. Corre `python tests/test_daily.py` antes de subir.
2. Javier aprueba subir a `main` dentro de sus pedidos; si te asignan otra rama, úsala y abre PR.
3. Después de subir, copia los archivos al espejo: `cp` de cada archivo de `git ls-files` a `/mnt/project-files/terminal/backend/`.
4. Para probar en Actions: dispara el workflow con `gh api repos/javiomt-dotcom/qx-backend/actions/workflows/<archivo>/dispatches -f ref=main`, y lee logs con la herramienta MCP de GitHub `get_job_logs` (el proxy bloquea descargar logs con `gh`).
5. Cambios de base de datos: usa las herramientas MCP de Supabase (inspecciona tablas antes) y actualiza `schema.sql` si la tabla es del back-end.
6. El contenedor no llega directo a `*.supabase.co` ni a `github.io`; usa MCP o Firecrawl para revisar.
7. Límites: Alpaca noticias ~200 req/min (no corras dos trabajos de noticias a la vez); IBKR 30 llamadas/min.
8. Toda idea nueva para el modelo se prueba primero con un backtest walk-forward de 3 años contra el swing actual y SPY; sólo entra si mejora más allá del ruido. Guarda el informe en `/mnt/project-files/terminal/research/AAAA-MM-DD-tema.md`.

## 11. Notas que no hay que olvidar

- La fila de TENB de la lista del 28-oct quedó en Supabase por decisión de Javier; ignórala.
- Alpha Vantage TOP_GAINERS_LOSERS se descartó (sólo 20 filas, casi todo warrants y centavos).
- NEWS_SENTIMENT de Alpha Vantage es de pago; por eso el tono es propio.
