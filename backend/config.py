import os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
GATEWAY_URL = os.environ["GATEWAY_URL"]
GATEWAY_SECRET = os.environ["GATEWAY_SECRET"]

DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "7"))
STATE_EXPIRY_SECONDS = float(os.environ.get("STATE_EXPIRY_SECONDS", str(60 * 60)))

# Datos de pago — reemplaza con los reales antes de producción.
YAPE_NUMBER = os.environ.get("YAPE_NUMBER", "[PON TU NÚMERO DE YAPE]")
YAPE_NAME = os.environ.get("YAPE_NAME", "[PON EL NOMBRE DE LA CUENTA]")