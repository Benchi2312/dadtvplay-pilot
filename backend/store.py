from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, STATE_EXPIRY_SECONDS
from classifier import normalize, KNOWN_PLATFORMS
import time

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def find_matching_products(text: str) -> list[dict]:
    """Devuelve TODOS los productos activos con stock que coinciden con
    alguna plataforma mencionada en el texto — puede ser más de uno si:
    (a) el cliente menciona varias plataformas, o
    (b) menciona una plataforma que tiene varios planes (ej. Disney
    Standar y Premium, o Hbo Max Estándar y Platino).
    Quien llama esta función decide si hay que pedir que elija uno."""
    result = supabase.table("products").select("*").eq("active", True).gt("stock", 0).execute()
    products = result.data if result else []
    if not products:
        return []

    norm = normalize(text)
    mentioned = [p for p in KNOWN_PLATFORMS if p in norm]
    if not mentioned:
        return []

    seen_ids = set()
    matches = []
    for kw in mentioned:
        for p in products:
            if kw in normalize(str(p["platform"])) and p["id"] not in seen_ids:
                matches.append(p)
                seen_ids.add(p["id"])
    return matches


def find_available_product(text: str):
    """Compatibilidad hacia atrás: si hay exactamente un match, lo
    devuelve. Si hay ambigüedad (0 o varios), devuelve None — quien
    llama debe usar find_matching_products directamente si quiere
    manejar la ambigüedad en vez de solo el caso simple."""
    matches = find_matching_products(text)
    return matches[0] if len(matches) == 1 else None


def get_available_platforms() -> list[str]:
    result = supabase.table("products").select("platform").eq("active", True).gt("stock", 0).execute()
    data = result.data if result else []
    return sorted(set(p["platform"] for p in data))


def log_conversation(customer_phone: str, incoming_message: str, intent: str, reply_message: str, confidence: float):
    supabase.table("conversation_log").insert({
        "customer_phone": customer_phone,
        "incoming_message": incoming_message,
        "intent": intent,
        "reply_message": reply_message,
    }).execute()


def get_conversation_state(phone: str):
    # NOTA: supabase-py puede devolver None directamente (no un objeto con
    # .data=None) cuando maybe_single() no encuentra filas. Por eso
    # chequeamos "result" antes de acceder a ".data".
    result = supabase.table("conversation_state").select("*").eq("phone", phone).maybe_single().execute()
    data = result.data if result else None
    if not data:
        return None
    updated_at = data.get("updated_at")
    if updated_at:
        # Supabase devuelve ISO 8601 — comparamos contra ahora en segundos.
        from datetime import datetime, timezone
        updated_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
        if time.time() - updated_ts > STATE_EXPIRY_SECONDS:
            return None
    return data


def set_conversation_state(phone: str, state: str, product_id: int | None = None):
    supabase.table("conversation_state").upsert({
        "phone": phone,
        "state": state,
        "pending_product_id": product_id,
        "updated_at": "now()",
    }).execute()