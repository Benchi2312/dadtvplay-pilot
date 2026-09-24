import asyncio
import re
import time
from fastapi import FastAPI, HTTPException, Request

from config import DEBOUNCE_SECONDS, KAPSO_WEBHOOK_SECRET
from kapso_client import send_whatsapp_message, send_platform_list
from classifier import classify_intent, normalize, KNOWN_PLATFORMS
from responder import build_reply, build_offer_reply, build_confirmation_reply, build_rejection_reply, build_attachment_reply
from store import (
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


def _pending_products(pending_state: dict) -> list[dict]:
    """Recupera los productos pendientes de un estado de conversación:
    usa pending_product_ids si existe, si no pending_product_id."""
    pids = pending_state.get("pending_product_ids") or []
    pid = pending_state.get("pending_product_id")
    ids = [i for i in pids if i] if pids else ([pid] if pid else [])
    return get_products_by_ids(ids)


def _same_platform_products(product: dict | None) -> list[dict]:
    """Todos los productos activos con stock de la MISMA plataforma que
    'product'. Sirve para entender un cambio de plan sin repetir el
    nombre de la plataforma (ej. estamos confirmando 'Disney Standar' y
    el cliente escribe 'premium')."""
    if not product:
        return []
    platform = product.get("platform")
    if not platform:
        return []
    from store import supabase
    res = supabase.table("products").select("*").eq("active", True).gt("stock", 0).eq("platform", platform).execute()
    return res.data if res else []


def _options_reply(products: list[dict], intro: str = "Tienes más de una opción disponible 😊:") -> str:
    if not products:
        return f"{intro}\nPor ahora no encuentro opciones con stock. ¿Quieres que te avisemos cuando vuelva a haber?"
    options = "\n".join(f"{i + 1}. {p['platform']} {p['plan_name']}: S/{p['price']:.2f}" for i, p in enumerate(products))
    return f"{intro}\n{options}\n¿Cuál te gustaría?"


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
    # Red de seguridad: si algo falla (ej. columna faltante en Supabase, red),
    # logueamos el traceback en Render y respondemos algo en vez de quedarnos
    # mudos (que es lo que pasa cuando la excepción mata la tarea en silencio).
    try:
        await _flush_after_silence_inner(phone)
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            await _send_whatsapp(phone, "Uy, se me trabó un momento 🙏, te respondo en un segundito.")
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

    # --- CASO 3: adjunto mientras se espera confirmación = confirmación implícita ---
    # El cliente manda la captura de pago directo, sin escribir "sí" antes.
    # Se interpreta como confirmación (asumimos que una foto en este punto
    # es comprobante de pago, dado el rubro del negocio).
    if pending_state and pending_state.get("state") == "awaiting_confirmation" and had_attachment:
        products = _pending_products(pending_state)
        intent = "pedido"
        reply = build_confirmation_reply(products)
        pid = products[0]["id"] if products else pending_state.get("pending_product_id")
        pids = [p["id"] for p in products] or None
        set_conversation_state(phone, "confirmed", pid, pids)

    # --- Texto mientras se espera confirmación (sí / no / cambio de producto / ambiguo) ---
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
            # ¿El cliente pidió algo distinto mientras confirmamos otro?
            matched = find_matching_products(combined)
            picked, all_specific = resolve_selection(combined, matched)
            if len(picked) > 1:
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
                # Sin plataforma nueva: ¿cambió de PLAN de la misma plataforma
                # que ya estaba confirmando? (ej. "premium" después de "Standar")
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
                    # Ni plan de la misma plataforma. ¿Mencionó un plan/precio
                    # de OTRA plataforma ("el de 26", "premium")? Expandimos
                    # al catálogo completo antes de rendirnos.
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
                        # Genuinamente ambiguo (ej. "Sii", "sip", emoji suelto).
                        intent = "pedido"
                        reply = "Disculpa, ¿eso es un sí? 🙂 Solo necesito que confirmes y te paso los datos del pago."

    # --- Elección entre varias opciones ya ofrecidas (multi-plan o multi-plataforma) ---
    if reply is None and pending_state and pending_state.get("state") == "awaiting_choice" and combined:
        # Solo opciones que siguen activas y con stock (las que se agotaron
        # ya no se ofrecen).
        candidates = [p for p in _pending_products(pending_state) if p.get("active") and (p.get("stock") or 0) > 0]
        if not candidates:
            # Las opciones que le mostramos ya no tienen stock / se
            # desactivaron: soltamos el contexto y seguimos con la
            # clasificación genérica de abajo.
            set_conversation_state(phone, "idle", None)
            reply = None
        elif is_real_affirmative(combined):
            # "Sí" sobre una lista ya reducida a productos concretos
            # (cada plataforma aparece una sola vez) = confirmar el conjunto.
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
            mentioned_platforms = {kw for kw in KNOWN_PLATFORMS if kw in normalize(combined)}
            picked_platforms = {normalize(str(p["platform"])) for p in picked}

            if (
                len(picked) > 1
                and all_specific
                and len(mentioned_platforms) >= 2
                and len(picked_platforms) >= 2
            ):
                # Pedido concreto de varios productos distintos (ej. "hbo
                # estándar y disney de 15.6 soles"): confirmamos el conjunto.
                intent = "pedido"
                reply = _multi_order_reply(picked)
                set_conversation_state(phone, "awaiting_confirmation", picked[0]["id"], [p["id"] for p in picked])
            elif len(picked) == 1:
                product = picked[0]
                intent = "pedido"
                reply = build_offer_reply(product, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            elif len(picked) > 1:
                # Sigue habiendo varias: reducimos la lista y volvemos a preguntar.
                intent = "pedido"
                reply = _options_reply(picked, "Perfecto, tengo varias opciones para eso 😊:")
                set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
            else:
                # No eligió ninguna de las opciones mostradas. ¿Pidió otra
                # plataforma o un plan concreto ("premium", "el de 26") que no
                # estaba en la lista reducida? Lo resolvemos contra TODO el
                # catálogo para no dejarlo atrapado.
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

    # --- Texto mientras el pedido ya está "confirmed" ---
    if reply is None and pending_state and pending_state.get("state") == "confirmed" and combined:
        t_norm = normalize(combined)
        if PAYMENT_FOLLOWUP_REGEX.search(t_norm):
            products = _pending_products(pending_state)
            intent = "pedido"
            reply = build_confirmation_reply(products)
        else:
            # CASO 1: ¿el cliente está pidiendo un producto DISTINTO
            # después de ya haber confirmado otro? Detectamos mención de
            # plataforma y, si la hay, tratamos esto como un pedido nuevo
            # que reemplaza al anterior.
            matched = find_matching_products(combined)
            picked, all_specific = resolve_selection(combined, matched)
            if len(picked) == 1:
                intent = "pedido"
                product = picked[0]
                reply = build_offer_reply(product, is_delayed)
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            elif len(picked) > 1:
                intent = "pedido"
                reply = _options_reply(picked)
                set_conversation_state(phone, "awaiting_choice", None, [p["id"] for p in picked])
            else:
                # Sin plataforma nueva: ¿cambió de plan de la misma
                # plataforma que acababa de confirmar?
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
            picked, all_specific = resolve_selection(combined, matched)
            mentioned_platforms = {kw for kw in KNOWN_PLATFORMS if kw in normalize(combined)}
            picked_platforms = {normalize(str(p["platform"])) for p in picked}
            if (
                len(picked) > 1
                and all_specific
                and len(mentioned_platforms) >= 2
                and len(picked_platforms) >= 2
            ):
                # Pedido concreto de varios productos desde el primer mensaje
                # (ej. "netflix y disney standar"): ofrecemos el conjunto.
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