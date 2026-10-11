import os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

WAMUNDO_SECRET = os.environ["WAMUNDO_SECRET"]
WAMUNDO_ACCOUNT_ID = os.environ["WAMUNDO_ACCOUNT_ID"]
WAMUNDO_HOOK_TOKEN = os.environ["WAMUNDO_HOOK_TOKEN"]

DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "7"))
STATE_EXPIRY_SECONDS = float(os.environ.get("STATE_EXPIRY_SECONDS", str(10 * 60)))

YAPE_NUMBER = os.environ.get("YAPE_NUMBER", "[PON TU NÚMERO DE YAPE]")
YAPE_NAME = os.environ.get("YAPE_NAME", "[PON EL NOMBRE DE LA CUENTA]")

MAX_MESSAGE_AGE_SECONDS = int(os.environ.get("MAX_MESSAGE_AGE_SECONDS", "600"))
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "0"))

# Mientras las variables de WaMundo tengan el valor de relleno "pendiente",
# el bot NO procesa ni responde nada (evita contestar con credenciales falsas
# o a la cuenta equivocada).
_PLACEHOLDERS = {"", "pendiente"}
WAMUNDO_CONFIGURED = all(
    v.strip().lower() not in _PLACEHOLDERS for v in (WAMUNDO_SECRET, WAMUNDO_ACCOUNT_ID)
)