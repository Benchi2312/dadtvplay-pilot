"""
Cliente para la API de WaMundo (no oficial). Dos responsabilidades:
enviar mensajes, y consultar los recibidos — WaMundo no empuja webhooks
con el contenido del mensaje de forma confiable (su Hook solo sirve como
aviso; el campo {{phone}} que manda no es el número real del cliente),
así que el webhook de este backend usa el Hook como disparador y luego
pregunta acá qué llegó de verdad.
"""
import json

import httpx

from config import WAMUNDO_SECRET, WAMUNDO_ACCOUNT_ID, WAMUNDO_CONFIGURED

API = "https://wamundo.com/api"


async def get_received_chats(limit: int = 30) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            res = await client.get(
                f"{API}/get/wa.received",
                params={"secret": WAMUNDO_SECRET, "limit": str(min(limit, 30))},
            )
            if res.status_code == 429:
                print("[WAMUNDO] wa.received 429 (rate limit): se reintenta en la próxima pasada")
                return []
            body = res.json()
        except Exception as e:
            print(f"[WAMUNDO] error consultando wa.received: {e}")
            return []
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    # Orden cronológico — importante para que el orden de los mensajes
    # agrupados por debounce refleje lo que el cliente escribió.
    return sorted(data, key=lambda c: c.get("created", 0))


async def send_whatsapp_message(to: str, body: str) -> dict:
    if not WAMUNDO_CONFIGURED:
        print("[WAMUNDO] sin configurar: no se envía")
        return {"ok": False, "status": 0, "body": "not configured"}
    fields = {
        "secret": (None, WAMUNDO_SECRET),
        "account": (None, WAMUNDO_ACCOUNT_ID),
        "recipient": (None, to),
        "type": (None, "text"),
        "message": (None, body),
    }
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            res = await client.post(f"{API}/send/whatsapp", files=fields)
        except Exception as e:
            print(f"[WAMUNDO] error enviando a {to}: {e}")
            return {"ok": False, "status": 0, "body": str(e)}
    text = res.text
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    ok = (
        res.status_code < 300
        and isinstance(parsed, dict)
        and parsed.get("status") == 200
        and parsed.get("data") is not False
    )
    print(f"[WAMUNDO] envío a {to}: http={res.status_code} ok={ok} body={text[:300]}")
    if not ok:
        print("[WAMUNDO] rechazado. Si dice 'account doesn't exist', revisar WAMUNDO_ACCOUNT_ID "
              "(el Unique ID cambia si se re-vincula el WhatsApp).")
    return {"ok": ok, "status": (parsed or {}).get("status", res.status_code), "body": text}


async def send_platform_list(to: str, body_text: str, platforms: list[str]) -> dict:
    """WaMundo no soporta listas interactivas de Meta — se manda como texto
    con viñetas. El cliente puede responder el nombre o el número de la opción."""
    lines = "\n".join(f"• {p}" for p in platforms)
    return await send_whatsapp_message(to, f"{body_text}\n\n{lines}\n\nEscríbeme cuál te interesa 👇")
