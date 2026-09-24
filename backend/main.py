import asyncio
import re
import time
from fastapi import FastAPI, HTTPException, Request

from config import DEBOUNCE_SECONDS, KAPSO_WEBHOOK_SECRET
from kapso_client import send_whatsapp_message, send_platform_list
from classifier import classify_intent, normalize
from responder import build_reply, build_confirmation_reply, build_rejection_reply, build_attachment_reply
from store import log_conversation, find_available_product, get_conversation_state, set_conversation_state, find_matching_products
from confirmation import is_real_affirmative, is_real_negative

app = FastAPI(title="Backend del agente de WhatsApp")

# Estado en memoria: como este backend corre como proceso siempre activo
# (a diferencia de las Edge Functions), agrupar ráfagas de mensajes es
# mucho más simple — no hace falta ninguna tabla de "reclamo atómico".
# IMPORTANTE: esto solo funciona con un único worker (uvicorn sin
# --workers). Con múltiples workers, cada uno tendría su propia memoria y
# volvería la condición de carrera que ya resolvimos una vez.
_pending: dict[str, list[str]] = {}
_has_attachment: dict[str, bool] = {}
_debounce_tasks: dict[str, asyncio.Task] = {}
_last_activity: dict[str, float] = {}
# IDs de mensajes de WhatsApp ya procesados, para descartar reintentos
# del webhook (ej. si Render tardó en despertar y Kapso reintentó).
_processed_message_ids: set[str] = set()
# Nombres de contacto según el número de WhatsApp (vienen en cada webhook).
_contact_names: dict[str, str] = {}

MAX_DELAY_SECONDS = 30 * 60
PAYMENT_FOLLOWUP_REGEX = re.compile(r"\b(yape|numero|número|pag\w*|plin|cuenta|qr)\b")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhook/kapso")
async def kapso_webhook(request: Request):
    # Verificación simple por secreto compartido en query param, como
    # hacíamos con HOOK_TOKEN en la versión de WaMundo. Si Kapso firma los
    # webhooks con HMAC (X-Hub-Signature-256 u otro header), lo agregamos
    # después de confirmar el header exacto con un webhook real de prueba
    # — no lo inventamos a ciegas.
    secret = request.query_params.get("secret")
    if secret != KAPSO_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    body = await request.json()
    print("Webhook crudo de Kapso/Meta:", body)  # TEMPORAL: para confirmar el formato real, quitar después

    try:
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages", [])
        contacts = value.get("contacts", [])
    except (KeyError, IndexError):
        # Puede ser un evento de "status" (entregado/leído), no un mensaje nuevo.
        return {"ok": True, "ignored": True}

    for m in messages:
        phone = m.get("from") or m.get("from_user_id")
        if contacts:
            name = contacts[0].get("profile", {}).get("name")
            if name:
                _contact_names[phone] = name

        msg_type = m.get("type")
        if msg_type == "text":
            text = m.get("text", {}).get("body")
        elif msg_type == "interactive":
            interactive = m.get("interactive", {})
            selection = interactive.get("list_reply") or interactive.get("button_reply")
            text = selection.get("title") if selection else None
        else:
            text = None
        has_attachment = msg_type in ("image", "audio", "video", "document", "sticker")

        msg_id = m.get("id")
        if msg_id:
            if msg_id in _processed_message_ids:
                continue  # reintento del webhook, ya lo procesamos
            _processed_message_ids.add(msg_id)
            if len(_processed_message_ids) > 10_000:
                _processed_message_ids.clear()

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

    return {"ok": True}


async def _flush_after_silence(phone: str):
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

    # --- CASO 3: adjunto mientras se espera confirmación = confirmación implícita ---
    # El cliente manda la captura de pago directo, sin escribir "sí" antes.
    # Se interpreta como confirmación (asumimos que una foto en este punto
    # es comprobante de pago, dado el rubro del negocio).
    if pending_state and pending_state.get("state") == "awaiting_confirmation" and had_attachment:
        product = None
        product_id = pending_state.get("pending_product_id")
        if product_id:
            from store import supabase
            res = supabase.table("products").select("*").eq("id", product_id).maybe_single().execute()
            product = res.data if res else None
        intent = "pedido"
        reply = build_confirmation_reply(product)
        set_conversation_state(phone, "confirmed", product_id)

    # --- Texto mientras se espera confirmación (sí / no / cambio de producto / ambiguo) ---
    if reply is None and pending_state and pending_state.get("state") == "awaiting_confirmation" and combined:
        if is_real_affirmative(combined):
            product = None
            product_id = pending_state.get("pending_product_id")
            if product_id:
                res = None
                from store import supabase
                res = supabase.table("products").select("*").eq("id", product_id).maybe_single().execute()
                product = res.data if res else None
            intent = "pedido"
            reply = build_confirmation_reply(product)
            set_conversation_state(phone, "confirmed", product_id)
        elif is_real_negative(combined):
            intent = "spam"
            reply = build_rejection_reply()
            set_conversation_state(phone, "idle", None)
        else:
            t_check = normalize(combined)
            matched = find_matching_products(combined)
            if len(matched) > 1:
                options = "\n".join(f"- {p['platform']} {p['plan_name']}: S/{p['price']}" for p in matched)
                intent = "pedido"
                reply = f"Tienes más de una opción disponible 😊:\n{options}\n¿Cuál te gustaría?"
                set_conversation_state(phone, "idle", None)
            elif len(matched) == 1:
                product = matched[0]
                intent = "pedido"
                reply = build_reply("pedido", combined, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            else:
                intent = "pedido"
                reply = "Disculpa, ¿eso es un sí? 🙂 Solo necesito que confirmes y te paso los datos del pago."

    # --- Texto mientras el pedido ya está "confirmed" ---
    if reply is None and pending_state and pending_state.get("state") == "confirmed" and combined:
        t_norm = normalize(combined)
        if PAYMENT_FOLLOWUP_REGEX.search(t_norm):
            product_id = pending_state.get("pending_product_id")
            product = None
            if product_id:
                from store import supabase
                res = supabase.table("products").select("*").eq("id", product_id).maybe_single().execute()
                product = res.data if res else None
            intent = "pedido"
            reply = build_confirmation_reply(product)
        else:
            # CASO 1: ¿el cliente está pidiendo un producto DISTINTO
            # después de ya haber confirmado otro? Detectamos mención de
            # plataforma y, si la hay, tratamos esto como un pedido nuevo
            # que reemplaza al anterior.
            matched = find_matching_products(combined)
            if len(matched) == 1:
                intent = "pedido"
                product = matched[0]
                reply = build_reply("pedido", combined, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            elif len(matched) > 1:
                options = "\n".join(f"- {p['platform']} {p['plan_name']}: S/{p['price']}" for p in matched)
                intent = "pedido"
                reply = f"Tienes más de una opción disponible 😊:\n{options}\n¿Cuál te gustaría?"
                set_conversation_state(phone, "idle", None)
            # Si no hay match (0) ni es sobre el pago, dejamos
            # "reply" en None: cae a la clasificación genérica de abajo,
            # que ya NO borra el estado "confirmed" por mensajes sin
            # relación (fix anterior).

    # --- Clasificación genérica (sin oferta pendiente relevante) ---
    if reply is None and combined:
        result = classify_intent(combined)
        intent = result["intent"]
        name = _contact_names.get(phone)

        if intent == "pedido":
            matched = find_matching_products(combined)
            if len(matched) > 1:
                options = "\n".join(f"- {p['platform']} {p['plan_name']}: S/{p['price']}" for p in matched)
                reply = f"Tienes más de una opción disponible 😊:\n{options}\n¿Cuál te gustaría?"
                set_conversation_state(phone, "idle", None)
            elif len(matched) == 1:
                reply = build_reply(intent, combined, is_delayed, name=name)
                product = matched[0]
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            else:
                reply = build_reply(intent, combined, is_delayed, name=name)
                set_conversation_state(phone, "idle", None)
        else:
            reply = build_reply(intent, combined, is_delayed, name=name)
            if not (pending_state and pending_state.get("state") == "confirmed"):
                set_conversation_state(phone, "idle", None)

    # --- Adjunto genérico (sin oferta pendiente relevante — ej. ya se atendió arriba) ---
    if reply is None and had_attachment:
        is_awaiting = bool(pending_state and pending_state.get("state") == "awaiting_confirmation")
        reply = build_attachment_reply(is_awaiting)
        intent = "adjunto"

    if reply is None:
        return

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