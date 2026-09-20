"""
Cliente para la API de Kapso (proxy sobre WhatsApp Cloud API oficial de Meta).
"""
import httpx
from config import KAPSO_API_KEY, KAPSO_PHONE_NUMBER_ID

BASE_URL = f"https://api.kapso.ai/meta/whatsapp/v24.0/{KAPSO_PHONE_NUMBER_ID}/messages"


def _recipient_fields(to: str) -> dict:
    if "." in to:
        return {"recipient_type": "individual", "recipient": to}
    return {"to": to}


async def _post(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.post(
                BASE_URL,
                headers={"X-API-Key": KAPSO_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            print(f"Envío: {res.status_code} {res.text}")
            return {"ok": res.status_code < 300, "status": res.status_code, "body": res.text}
        except Exception as e:
            print(f"Error enviando: {e}")
            return {"ok": False, "status": 0, "body": str(e)}


async def send_whatsapp_message(to: str, body: str) -> dict:
    payload = {"messaging_product": "whatsapp", "type": "text", "text": {"body": body}}
    payload.update(_recipient_fields(to))
    return await _post(payload)


async def send_platform_list(to: str, body_text: str, platforms: list[str]) -> dict:
    """Manda una lista interactiva (hasta 10 opciones) con las plataformas
    disponibles. El cliente toca una opción en vez de escribir texto libre."""
    rows = [{"id": f"platform_{p.lower().replace(' ', '_')}", "title": p} for p in platforms[:10]]
    payload = {
        "messaging_product": "whatsapp",
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": "Ver opciones",
                "sections": [{"title": "Disponibles", "rows": rows}],
            },
        },
    }
    payload.update(_recipient_fields(to))
    return await _post(payload)