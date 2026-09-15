import os

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

KAPSO_API_KEY = os.environ["KAPSO_API_KEY"]
KAPSO_PHONE_NUMBER_ID = os.environ["KAPSO_PHONE_NUMBER_ID"]

# Secreto compartido para verificar que los webhooks entrantes vienen
# realmente de Kapso (lo defines tú al registrar el webhook en su
# dashboard/CLI, y debe coincidir con lo que pongas aquí).
KAPSO_WEBHOOK_SECRET = os.environ["KAPSO_WEBHOOK_SECRET"]

DEBOUNCE_SECONDS = float(os.environ.get("DEBOUNCE_SECONDS", "7"))
STATE_EXPIRY_SECONDS = float(os.environ.get("STATE_EXPIRY_SECONDS", str(60 * 60)))

YAPE_NUMBER = os.environ.get("YAPE_NUMBER", "[PON TU NÚMERO DE YAPE]")
YAPE_NAME = os.environ.get("YAPE_NAME", "[PON EL NOMBRE DE LA CUENTA]")