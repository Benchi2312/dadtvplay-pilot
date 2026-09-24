from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, STATE_EXPIRY_SECONDS
from classifier import normalize, KNOWN_PLATFORMS
import difflib
import re
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


def get_products_by_ids(ids: list[int]) -> list[dict]:
    """Trae los productos activos según sus ids, respetando el orden
    pedido (importante para no mezclar el orden de las opciones que ya
    le mostramos al cliente). Devuelve [] si no hay ids."""
    valid = [i for i in (ids or []) if i]
    if not valid:
        return []
    result = supabase.table("products").select("*").in_("id", valid).execute()
    data = result.data if result else []
    by_id = {p["id"]: p for p in data if p.get("id") is not None}
    return [by_id[i] for i in dict.fromkeys(valid) if i in by_id]


# Patrón de números sueltos ("de 15.6 soles", "26.00", "15.6") para
# detectar selección por precio.
_PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _round2(value) -> float:
    return round(float(value), 2)


def _plan_mentioned(plan_name, norm: str) -> bool:
    """¿El mensaje menciona este nombre de plan? Tolera variantes
    ortográficas (standar / estándar / standard) y acentos — el acento ya
    lo quita normalize()."""
    nplan = normalize(plan_name)
    if not nplan:
        return False
    if nplan in norm:
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", norm) if w]
    # alguna palabra del mensaje es similar al plan completo
    if any(difflib.SequenceMatcher(None, nplan, w).ratio() >= 0.8 for w in words):
        return True
    # o el mensaje contiene alguna palabra clave del plan (ej. "full" para
    # "Full con Liga1 Max", "sin", "canales"...)
    plan_words = [w for w in re.split(r"[^a-z0-9]+", nplan) if len(w) >= 3]
    return any(w in words for w in plan_words)


def _price_mentioned(price, norm: str) -> bool:
    target = _round2(price)
    for raw in _PRICE_RE.findall(norm):
        candidate = _round2(raw.replace(",", "."))
        if candidate == target:
            return True
    return False


def resolve_selection(text: str, candidates: list[dict]) -> tuple[list[dict], bool]:
    """Dado el texto del cliente y una lista de opciones ya ofrecidas,
    devuelve:
      (picked, all_specific)
    - picked: las candidatas que el cliente claramente elige (por
      plataforma y/o plan y/o precio).
    - all_specific: True si TODAS las elegidas fueron nombradas con
      detalle (plan o precio), no solo por su plataforma. Sirve para
      distinguir un pedido concreto ("hbo estándar y disney de 15.6
      soles") de una mención ambigua ("quiero hbo")."""
    norm = normalize(text)
    if not norm:
        return [], False

    mentioned_ids = {
        p["id"]
        for p in candidates
        if any(kw in norm for kw in KNOWN_PLATFORMS if kw in normalize(str(p.get("platform", ""))))
    }

    if not mentioned_ids:
        picked = [
            p for p in candidates
            if _plan_mentioned(str(p.get("plan_name", "")), norm) or _price_mentioned(p.get("price", 0), norm)
        ]
        return picked, bool(picked)

    picked = [p for p in candidates if p["id"] in mentioned_ids]
    said_detail = any(
        _plan_mentioned(str(p.get("plan_name", "")), norm) or _price_mentioned(p.get("price", 0), norm)
        for p in picked
    )
    if not said_detail:
        return picked, False

    narrowed = [
        p for p in picked
        if _plan_mentioned(str(p.get("plan_name", "")), norm) or _price_mentioned(p.get("price", 0), norm)
    ]
    if not narrowed:
        return picked, False
    # Cada elemento de "narrowed" fue incluido por detalle (plan o precio),
    # así que si hay ≥1 son elecciones específicas.
    return narrowed, True


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


def set_conversation_state(phone: str, state: str, product_id: int | None = None, product_ids: list[int] | None = None):
    if state == "idle":
        product_id = None
        product_ids = None
    if state == "awaiting_confirmation" and product_id is not None and product_ids is None:
        product_ids = [product_id]

    supabase.table("conversation_state").upsert({
        "phone": phone,
        "state": state,
        "pending_product_id": product_id,
        "pending_product_ids": product_ids,
        "updated_at": "now()",
    }).execute()