import asyncio
import re
import time
from fastapi import FastAPI, HTTPException, Request

from config import (
    DEBOUNCE_SECONDS,
    WAMUNDO_HOOK_TOKEN,
    WAMUNDO_ACCOUNT_ID,
    WAMUNDO_CONFIGURED,
    MAX_MESSAGE_AGE_SECONDS,
    POLL_INTERVAL_SECONDS,
)
from wamundo_client import send_whatsapp_message, send_platform_list, get_received_chats
from classifier import classify_intent, normalize, KNOWN_PLATFORMS
from responder import build_reply, build_offer_reply, build_confirmation_reply, build_rejection_reply, build_attachment_reply
from store import (
    supabase,
    log_conversation,
    get_conversation_state,
    set_conversation_state,
    find_matching_products,
    get_all_available_products,
    resolve_selection,
    get_products_by_ids,
)
from confirmation import is_real_affirmative, is_real_negative

app = FastAPI(title="Backend del agente de WhatsApp")

# Estado en memoria: solo válido con un único worker de uvicorn
# (sin --workers).
_pending: dict[str, list[str]] = {}
_has_attachment: dict[str, bool] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}
_last_activity: dict[str, float] = {}

MAX_DELAY_SECONDS = 30 * 60
PAYMENT_FOLLOWUP_REGEX = re.compile(r"\b(yape|numero|número|pag\w*|plin|cuenta|qr)\b")

def _pending_products(pending_state: dict) -> list[dict]:
    pids = pending_state.get("pending_product_ids") or []
    pid = pending_state.get("pending_product_id")
    ids = [i for i in pids if i] if pids else ([pid] if pid else [])
    return get_products_by_ids(ids)


def _same_platform_products(product: dict | None) -> list[dict]:
    if not product:
        return []
    platform = product.get("platform")
    if not platform:
        return []
    res = supabase.table("products").select("*").eq("active", True).gt("stock", 0).eq("platform", platform).execute()
    return res.data if res else []


def _options_reply(products: list[dict], intro: str = "Tienes más de una opción disponible 😊:") -> str:
    if not products:
        return f"{intro}\nPor ahora no encuentro opciones con stock. ¿Quieres que te avisemos cuando vuelva a haber?"
    options = "\n".join(f"{i + 1}. {p['platform']} {p['plan_name']}: S/{p['price']:.2f}" for i, p in enumerate(products))
    return f"{intro}\n{options}\n¿Cuál te gustaría?"


_SELECT_ALL_RE = re.compile(r"\b(los\s*(2|dos)|las\s*(2|dos)|amb[oa]s|tod[oa]s?|todit[oa]s?)\b")


def _is_multi_specific(picked: list[dict], combined: str) -> bool:
    mentioned_platforms = {kw for kw in KNOWN_PLATFORMS if kw in normalize(combined)}
    picked_platforms = {normalize(str(p["platform"])) for p in picked}
    return (
        len(picked) > 1
        and len(mentioned_platforms) >= 2
        and len(picked_platforms) >= 2
    )


def _multi_order_reply(products: list[dict]) -> str:
    lines = "\n".join(f"- {p['platform']} {p['plan_name']}: S/{p['price']:.2f}" for p in products)
    total = sum(float(p["price"]) for p in products)
    return (
        f"Perfecto, te armo el pedido con estos productos 😊:\n{lines}\n"
        f"*Total: S/{total:.2f}*. ¿Confirmamos? Te paso el link de pago por Yape/Plin."
    )


@app.get("/health")
def health():
    return {"ok": True}


async def _claim_message(wamundo_id) -> bool:
    """True si este proceso 'ganó' el derecho a responder el mensaje (insert
    exitoso). False si ya estaba reclamado. Un error inesperado se registra
    (antes fallaba en silencio) y se trata como 'no reclamado'."""
    try:
        supabase.table("processed_messages").insert({"wamundo_id": str(wamundo_id)}).execute()
        return True
    except Exception as e:
        msg = str(e).lower()
        if "23505" in msg or "duplicate" in msg:
            return False  # ya procesado
        print(f"[CLAIM] error inesperado con {wamundo_id}: {e}")
        return False


_fetch_lock = asyncio.Lock()
_bg_tasks: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _process_received() -> int:
    if not WAMUNDO_CONFIGURED:
        print("[WARN] WAMUNDO_SECRET / WAMUNDO_ACCOUNT_ID sin configurar: no se procesa nada")
        return 0

    async with _fetch_lock:  # serializa consultas: evita 429 si el Hook dispara en ráfaga
        chats = await get_received_chats()
        now = time.time()

        for chat in chats:
            if chat.get("group"):
                continue  # el bot es 1:1

            # SOLO la cuenta vinculada a este bot. Va ANTES de reclamar para no
            # registrar ni responder mensajes de otros números de la misma cuenta
            # de WaMundo (por ejemplo, el número principal de Diego).
            acct = chat.get("account_unique")
            if acct != WAMUNDO_ACCOUNT_ID:
                if acct is None:
                    print("[WARN] mensaje sin account_unique: se ignora por seguridad")
                continue

            created = chat.get("created") or 0
            if now - created > MAX_MESSAGE_AGE_SECONDS:
                continue  # historial viejo

            wamundo_id = chat.get("id")
            if wamundo_id is None:
                continue
            if not await _claim_message(wamundo_id):
                continue  # ya procesado

            phone = chat.get("reply_to")  # nunca 'recipient'
            text = chat.get("message")
            has_attachment = bool(chat.get("attachment"))
            if not phone or (not text and not has_attachment):
                continue

            _pending.setdefault(phone, [])
            if text:
                _pending[phone].append(text)
            if has_attachment:
                _has_attachment[phone] = True
            _last_activity[phone] = time.time()

            existing = _debounce_tasks.get(phone)
            if existing and not existing.done():
                existing.cancel()
            _debounce_tasks[phone] = asyncio.create_task(_flush_after_silence(phone))

        await asyncio.sleep(2)  # pausa corta entre consultas
        return len(chats)


# WaMundo llama a los Hooks con GET. Responde de inmediato y procesa en segundo
# plano, para que el Hook no se quede esperando ni dé timeout.
@app.get("/webhook/wamundo-hook")
async def wamundo_hook(request: Request):
    if request.query_params.get("token") != WAMUNDO_HOOK_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")
    _spawn(_process_received())
    return {"ok": True}


async def _poll_loop():
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            await _process_received()
        except Exception as e:
            print(f"[POLL] error: {e}")


@app.on_event("startup")
async def _on_startup():
    # Solo booleanos y números, nunca valores de secretos.
    print(
        f"[STARTUP] wamundo_configured={WAMUNDO_CONFIGURED} "
        f"poll_interval={POLL_INTERVAL_SECONDS}s max_age={MAX_MESSAGE_AGE_SECONDS}s"
    )
    if POLL_INTERVAL_SECONDS > 0:
        _spawn(_poll_loop())


async def _flush_after_silence(phone: str):
    try:
        await _flush_after_silence_inner(phone)
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            await _send_whatsapp(phone, "Uy, se me trabó un momento 🙏. Escríbeme de nuevo, por favor.")
        except Exception:
            pass


async def _flush_after_silence_inner(phone: str):
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return

    messages = _pending.pop(phone, [])
    had_attachment = _has_attachment.pop(phone, False)
    last_time = _last_activity.pop(phone, time.time())

    combined = " / ".join(messages)
    if not combined and not had_attachment:
        return

    is_delayed = (time.time() - last_time) > MAX_DELAY_SECONDS
    pending_state = get_conversation_state(phone) if (combined or had_attachment) else None

    intent = "spam"
    reply = None

    if pending_state and pending_state.get("state") == "awaiting_confirmation" and had_attachment:
        products = _pending_products(pending_state)
        intent = "pedido"
        reply = build_confirmation_reply(products)
        pid = products[0]["id"] if products else pending_state.get("pending_product_id")
        pids = [p["id"] for p in products] or None
        set_conversation_state(phone, "confirmed", pid, pids)

    if reply is None and pending_state and pending_state.get("state") == "awaiting_confirmation" and combined:
        if is_real_affirmative(combined):
            products = _pending_products(pending_state)
            intent = "pedido"
            reply = build_confirmation_reply(products)
            pid = products[0]["id"] if products else pending_state.get("pending_product_id")
            pids = [p["id"] for p in products] or None
            set_conversation_state(phone, "confirmed", pid, pids)
        elif is_real_negative(combined):
            intent = "spam"
            reply = build_rejection_reply()
            set_conversation_state(phone, "idle", None)
        else:
            if _SELECT_ALL_RE.search(normalize(combined)):
                pending_prods = _pending_products(pending_state)
                if len(pending_prods) > 1:
                    intent = "pedido"
                    reply = _multi_order_reply(pending_prods)
                    set_conversation_state(phone, "awaiting_confirmation", pending_prods[0]["id"], [p["id"] for p in pending_prods])
            if reply is None:
                matched = find_matching_products(combined)
                picked, all_specific = resolve_selection(combined, matched)
                if len(picked) > 1 and all_specific and _is_multi_specific(picked, combined):
                    intent = "pedido"
                    reply = _multi_order_reply(picked)
                    set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"], [p["id"] for p in picked])
                elif len(picked) > 1:
                    options = _options_reply(picked)
                    intent = "pedido"
                    reply = options
                    set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
                elif len(picked) == 1:
                    product = picked[0]
                    intent = "pedido"
                    reply = build_offer_reply(product, is_delayed)
                    set_conversation_state(phone, "awaiting_confirmation", product["id"])
            else:
                pending_prods = _pending_products(pending_state)
                siblings = _same_platform_products(pending_prods[0] if pending_prods else None)
                alt, _ = resolve_selection(combined, siblings)
                if len(alt) == 1:
                    product = alt[0]
                    intent = "pedido"
                    reply = build_offer_reply(product, is_delayed)
                    set_conversation_state(phone, "awaiting_confirmation", product["id"])
                elif len(alt) > 1:
                    intent = "pedido"
                    reply = _options_reply(alt, "De esa plataforma tengo estas opciones 😊:")
                    set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in alt])
                else:
                    all_prods = get_all_available_products()
                    alt2, _ = resolve_selection(combined, all_prods)
                    if len(alt2) == 1:
                        product = alt2[0]
                        intent = "pedido"
                        reply = build_offer_reply(product, is_delayed)
                        set_conversation_state(phone, "awaiting_confirmation", product["id"])
                    elif len(alt2) > 1:
                        intent = "pedido"
                        reply = _options_reply(alt2)
                        set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in alt2])
                    else:
                        intent = "pedido"
                        reply = "Disculpa, ¿eso es un sí? 🙂 Solo necesito que confirmes y te paso los datos del pago."

    if reply is None and pending_state and pending_state.get("state") == "awaiting_choice" and combined:
        candidates = [p for p in _pending_products(pending_state) if p.get("active") and (p.get("stock") or 0) > 0]
        mentioned_kws = {kw for kw in KNOWN_PLATFORMS if kw in normalize(combined)}
        if mentioned_kws:
            fresh = find_matching_products(combined)
            if fresh:
                candidates = fresh
        if not candidates:
            set_conversation_state(phone, "idle", None)
            reply = None
        elif _SELECT_ALL_RE.search(normalize(combined)):
            if is_real_affirmative(combined):
                intent = "pedido"
                reply = build_confirmation_reply(candidates)
                set_conversation_state(phone, "confirmed", candidates[0]["id"], [p["id"] for p in candidates])
            else:
                intent = "pedido"
                reply = _multi_order_reply(candidates)
                set_conversation_state(phone, "awaiting_confirmation", candidates[0]["id"], [p["id"] for p in candidates])
        elif is_real_affirmative(combined):
            unique_platforms = {p["platform"] for p in candidates}
            if len(candidates) == 1:
                intent = "pedido"
                reply = build_offer_reply(candidates[0], is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", candidates[0]["id"])
            elif len(unique_platforms) == len(candidates):
                intent = "pedido"
                reply = _multi_order_reply(candidates)
                set_conversation_state(phone, "awaiting_confirmation", candidates[0]["id"], [p["id"] for p in candidates])
            else:
                reply = None
        else:
            picked, all_specific = resolve_selection(combined, candidates)

            if len(picked) > 1 and all_specific and _is_multi_specific(picked, combined):
                intent = "pedido"
                reply = _multi_order_reply(picked)
                set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"], [p["id"] for p in picked])
            elif len(picked) == 1:
                product = picked[0]
                intent = "pedido"
                reply = build_offer_reply(product, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            elif len(picked) > 1:
                intent = "pedido"
                reply = _options_reply(picked, "Perfecto, tengo varias opciones para eso 😊:")
                set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
            else:
                all_prods = get_all_available_products()
                alt, _ = resolve_selection(combined, all_prods)
                if len(alt) == 1:
                    intent = "pedido"
                    reply = build_offer_reply(alt[0], is_delayed)
                    set_conversation_state(phone, "awaiting_confirmation", alt[0]["id"])
                elif len(alt) > 1:
                    intent = "pedido"
                    reply = _options_reply(alt)
                    set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in alt])
                else:
                    intent = "pedido"
                    reply = _options_reply(candidates, "No encontré esa opción. Estas son las disponibles 😊:")
                    set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in candidates])

    if reply is None and pending_state and pending_state.get("state") == "confirmed" and combined:
        t_norm = normalize(combined)
        if PAYMENT_FOLLOWUP_REGEX.search(t_norm):
            products = _pending_products(pending_state)
            intent = "pedido"
            reply = build_confirmation_reply(products)
        else:
            matched = find_matching_products(combined)
            picked, all_specific = resolve_selection(combined, matched)
            if len(picked) > 1 and all_specific and _is_multi_specific(picked, combined):
                intent = "pedido"
                reply = _multi_order_reply(picked)
                set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"], [p["id"] for p in picked])
            elif len(picked) == 1:
                intent = "pedido"
                product = picked[0]
                reply = build_offer_reply(product, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            elif len(picked) > 1:
                intent = "pedido"
                reply = _options_reply(picked)
                set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
            else:
                pending_prods = _pending_products(pending_state)
                siblings = _same_platform_products(pending_prods[0] if pending_prods else None)
                alt, _ = resolve_selection(combined, siblings)
                if len(alt) == 1:
                    intent = "pedido"
                    reply = build_offer_reply(alt[0], is_delayed)
                    set_conversation_state(phone, "awaiting_confirmation", alt[0]["id"])
                elif len(alt) > 1:
                    intent = "pedido"
                    reply = _options_reply(alt, "De esa plataforma tengo estas opciones 😊:")
                    set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in alt])

    if reply is None and combined:
        result = classify_intent(combined)
        intent = result["intent"]

        if intent == "pedido":
            matched = find_matching_products(combined)
            picked, all_specific = resolve_selection(combined, matched)
            if len(picked) > 1 and all_specific and _is_multi_specific(picked, combined):
                intent = "pedido"
                reply = _multi_order_reply(picked)
                set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"], [p["id"] for p in picked])
            elif len(picked) > 1:
                reply = _options_reply(picked)
                set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
            elif len(picked) == 1:
                reply = build_offer_reply(picked[0], is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"])
            else:
                reply = build_reply(intent, combined, is_delayed, name=None)
                set_conversation_state(phone, "idle", None)
        else:
            reply = build_reply(intent, combined, is_delayed, name=None)
            if not (pending_state and pending_state.get("state") == "confirmed"):
                set_conversation_state(phone, "idle", None)

    if reply is None and had_attachment:
        is_awaiting = bool(pending_state and pending_state.get("state") == "awaiting_confirmation")
        reply = build_attachment_reply(is_awaiting)
        intent = "adjunto"

    if reply is None:
        return

    prev_state = pending_state.get("state") if pending_state else None
    print(f"[FLUSH] {phone} estado={prev_state} intent={intent} reply={reply[:80]!r}")

    if intent == "consulta":
        from store import get_available_platforms
        platforms = get_available_platforms()
        if platforms:
            await send_platform_list(phone, reply, platforms)
        else:
            await _send_whatsapp(phone, reply)
    else:
        await _send_whatsapp(phone, reply)
    log_conversation(phone, combined or "[adjunto]", intent, reply, 1.0)


async def _send_whatsapp(phone: str, message: str):
    await send_whatsapp_message(phone, message)