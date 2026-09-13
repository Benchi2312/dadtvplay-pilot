import asyncio
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from config import GATEWAY_URL, GATEWAY_SECRET, DEBOUNCE_SECONDS
from classifier import classify_intent
from responder import build_reply, build_confirmation_reply, build_rejection_reply, build_attachment_reply
from store import log_conversation, find_available_product, get_conversation_state, set_conversation_state
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

MAX_DELAY_SECONDS = 30 * 60


class IncomingMessage(BaseModel):
    phone: str
    message: str | None = None
    attachment: bool = False
    timestamp: float | None = None


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhook/incoming")
async def incoming(msg: IncomingMessage, request: Request):
    secret = request.headers.get("x-gateway-secret")
    if secret != GATEWAY_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    if not msg.message and not msg.attachment:
        return {"ok": True, "ignored": True}

    phone = msg.phone
    if msg.message:
        _pending.setdefault(phone, []).append(msg.message)
    if msg.attachment:
        _has_attachment[phone] = True
    _last_activity[phone] = msg.timestamp or time.time()

    existing = _debounce_tasks.get(phone)
    if existing and not existing.done():
        existing.cancel()

    _debounce_tasks[phone] = asyncio.create_task(_flush_after_silence(phone))
    return {"ok": True, "queued": True}


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
    pending_state = get_conversation_state(phone) if combined else None

    intent = "spam"
    reply = None

    if pending_state and pending_state.get("state") == "awaiting_confirmation" and combined:
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
            set_conversation_state(phone, "idle", None)
        elif is_real_negative(combined):
            intent = "spam"
            reply = build_rejection_reply()
            set_conversation_state(phone, "idle", None)

    if reply is None and combined:
        result = classify_intent(combined)
        intent = result["intent"]
        reply = build_reply(intent, combined, is_delayed)

        if intent == "pedido":
            product = find_available_product(combined)
            if product:
                set_conversation_state(phone, "awaiting_confirmation", product["id"])
            else:
                set_conversation_state(phone, "idle", None)
        else:
            set_conversation_state(phone, "idle", None)

    if reply is None and had_attachment:
        is_awaiting = bool(pending_state and pending_state.get("state") == "awaiting_confirmation")
        reply = build_attachment_reply(is_awaiting)
        intent = "adjunto"

    if reply is None:
        return

    await _send_whatsapp(phone, reply)
    log_conversation(phone, combined or "[adjunto]", intent, reply, 1.0)


async def _send_whatsapp(phone: str, message: str):
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.post(
                f"{GATEWAY_URL}/send",
                headers={"x-gateway-secret": GATEWAY_SECRET},
                json={"phone": phone, "message": message},
            )
            print(f"Envío a {phone}: {res.status_code} {res.text}")
        except Exception as e:
            print(f"Error enviando a {phone}: {e}")