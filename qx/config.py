"""Parámetros del sistema. Los secretos llegan por variables de entorno, nunca en el código."""
import os

# --- secretos (secretos del repositorio en GitHub, o un .env local) ---
def env(name, default=""):
    return os.environ.get(name, default).strip()   # los secretos pegados a veces traen espacios o saltos de línea


ALPACA_KEY = env("ALPACA_KEY_ID")
ALPACA_SECRET = env("ALPACA_SECRET_KEY")
SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_KEY = env("SUPABASE_SERVICE_KEY")
TELEGRAM_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT = env("TELEGRAM_CHAT_ID")

# --- universo ---
MIN_PRICE = 5.0            # precio mínimo para entrar al universo
MIN_DOLLAR_VOL = 5e6       # promedio 20 días de dólares negociados
HISTORY_DAYS = 400         # días naturales de historial que se guardan
EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}

# --- modelo (mismos valores que la terminal) ---
PICKS = 10
TEST_DAYS = 60
HALF_LIFE = 120
REAL_W = 5
STOP_ATR = 1.5
TGT_ATR = 3.0
GAP_ATR = 1.0

# --- reglas de la lista (propuestas el 22-oct tras revisar 6 sesiones reales) ---
MAX_NON_SP = 3             # máximo de picks fuera del S&P 500
MAX_JUMP = 0.40            # descartar si subió más de 40% en el día de la señal (posible compra anunciada)

# --- riesgo (como en la terminal) ---
CAPITAL = float(os.environ.get("QX_CAPITAL", "10000"))
RISK = float(os.environ.get("QX_RISK", "0.01"))
MAX_POS = float(os.environ.get("QX_MAXPOS", "0.20"))
REGIME_MULT = {"fav": 1.0, "neu": 0.5, "weak": 0.25}

MODEL_NAME = "logit-v4-server"
