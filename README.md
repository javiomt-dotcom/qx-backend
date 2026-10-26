# QX Backend

Rutina diaria automática para la terminal QX. Corre sola en GitHub Actions después del cierre del mercado (lunes a viernes) y:

1. Baja los precios diarios de Alpaca: S&P 500 más acciones líquidas de todo el mercado (precio ≥ $5 y ≥ $5M diarios negociados).
2. Guarda las velas en Supabase (Postgres).
3. Califica la lista del día anterior con las reglas realistas: compra en la apertura, se salta si abre más de 1 ATR arriba, stop a 1.5 ATR y objetivo a 3 ATR.
4. Reentrena el modelo (también con sus propios resultados reales) y guarda la lista de 10 acciones para la siguiente sesión. Máximo 3 fuera del S&P y descarta las que subieron más de 40% en el día.
5. Te manda el resumen por Telegram.

No es asesoría financiera.

## Configuración (una sola vez)

### 1. Alpaca (precios, gratis)
1. Crea una cuenta en https://alpaca.markets y entra a **Paper Trading**.
2. En el panel, **API Keys → Generate**. Copia el *Key ID* y el *Secret Key*.

### 2. Supabase (base de datos, gratis)
1. Crea un proyecto en https://supabase.com.
2. Abre **SQL Editor**, pega el contenido de `schema.sql` y dale **Run**.
3. En **Project Settings → API** copia la *Project URL* y la llave **service_role** (no la `anon`).

### 3. Telegram (mensajes)
1. En Telegram habla con **@BotFather**, manda `/newbot` y copia el *token*.
2. Mándale cualquier mensaje a tu bot nuevo.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` en el navegador y copia el número de `"chat":{"id": ...}`.

### 4. Secretos en GitHub
En el repositorio: **Settings → Secrets and variables → Actions → New repository secret**. Crea estos seis:

| Secreto | Valor |
|---|---|
| `ALPACA_KEY_ID` | Key ID de Alpaca |
| `ALPACA_SECRET_KEY` | Secret Key de Alpaca |
| `SUPABASE_URL` | Project URL de Supabase |
| `SUPABASE_SERVICE_KEY` | llave service_role de Supabase |
| `TELEGRAM_BOT_TOKEN` | token de BotFather |
| `TELEGRAM_CHAT_ID` | tu chat id |

Opcional: `ALPHAVANTAGE_KEY` con tu llave gratis de https://www.alphavantage.co/support/#api-key. Con ella el swing no compra acciones que reportan resultados durante los 5 días.

Opcional, en la pestaña **Variables**: `QX_CAPITAL` (capital, para calcular acciones), `QX_RISK` (riesgo por operación, ej. `0.01`) y `QX_MAXPOS` (tope por posición, ej. `0.2`).

Nunca pegues estas llaves en un chat ni en el código.

### 5. Primera corrida
En **Actions → QX diario → Run workflow**. Si todo está bien, te llega el mensaje a Telegram. Después corre solo a las 21:30 y 22:30 UTC (la segunda es respaldo; si la lista ya existe no hace nada).

## Correr en tu computadora

```bash
pip install -r requirements.txt
python tests/test_daily.py          # pruebas con datos sintéticos
python daily.py --local             # usa SQLite (qx.sqlite) en lugar de Supabase
python daily.py --force             # rehace la lista del día aunque ya exista
```

## Estructura

- `daily.py`: la rutina completa.
- `qx/engine.py`: modelo (regresión logística con calibración), backtest y reglas de entrada.
- `qx/data_alpaca.py`: precios, activos y calendario de Alpaca.
- `qx/store.py`: Supabase y SQLite con la misma interfaz.
- `qx/notify.py`: mensajes de Telegram.
- `schema.sql`: tablas de la base de datos.
- `.github/workflows/daily.yml`: el horario automático.
