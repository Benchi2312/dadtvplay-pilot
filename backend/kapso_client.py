"""
Cliente mínimo para la API de Kapso (proxy sobre WhatsApp Cloud API oficial
de Meta). Reemplaza al gateway de Baileys/Node — esto es solo REST, sin
socket persistente que mantener.
"""
import httpx
from config import KAPSO_API_KEY, KAPSO_PHONE_NUMBER_ID

BASE_URL = f"https://api.kapso.ai/meta/whatsapp/{KAPSO_PHONE_NUMBER_ID}/messages"


async def send_whatsapp_message(to: str, body: str) -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "type": "text",
        "text": {"body": body},
    }
    if "." in to:
        payload["recipient_type"] = "individual"
        payload["recipient"] = to
    else:
        payload["to"] = to

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            res = await client.post(
                BASE_URL,
                headers={"X-API-Key": KAPSO_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            print(f"Envío a {to}: {res.status_code} {res.text}")
            return {"ok": res.status_code < 300, "status": res.status_code, "body": res.text}
        except Exception as e:
            print(f"Error enviando a {to}: {e}")
            return {"ok": False, "status": 0, "body": str(e)}