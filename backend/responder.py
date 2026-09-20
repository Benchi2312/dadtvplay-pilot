import random
from datetime import datetime
from zoneinfo import ZoneInfo
from config import YAPE_NUMBER, YAPE_NAME
from store import find_available_product


def time_greeting() -> str:
    hour = datetime.now(ZoneInfo("America/Lima")).hour
    if hour < 12:
        return "Buenos días"
    if hour < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _greet(name: str | None) -> str:
    saludo = time_greeting()
    return f"{saludo} {name}" if name else saludo


CONSULTA_VARIANTS = [
    "{greet}! 👋 Vendemos cuentas de Netflix, Disney+, HBO Max y Prime Video. ¿Cuál te interesa?",
    "{greet}, un gusto saludarte 😊. Manejamos Netflix, Disney+, HBO Max y Prime Video — ¿alguna te llama la atención?",
    "{greet}! Aquí tienes cuentas de streaming: Netflix, Disney+, HBO Max y Prime Video. ¿Con cuál te ayudo?",
]

RECLAMO_VARIANTS = [
    "Lamentamos el problema con tu cuenta 🙏. Un asesor te va a contactar en breve para ayudarte a resolverlo.",
    "Qué pena que estés teniendo este inconveniente 😕. Ya quedó anotado, un asesor te escribe pronto para solucionarlo.",
    "Gracias por avisarnos 🙏, vamos a revisarlo enseguida y un asesor te contacta en breve.",
]

OFENSIVO_VARIANTS = [
    "Vamos a resolver esto con calma 🙏. Te voy a conectar con un asesor para que te ayude directamente.",
    "Entiendo la molestia 🙏. Un asesor te va a atender personalmente para resolverlo.",
]

FUERA_DE_TEMA_VARIANTS = [
    "Por aquí solo vendemos cuentas de streaming (Netflix, Disney+, HBO Max, Prime Video) — no manejamos ese producto. ¿Te ayudo con alguna cuenta?",
    "Ese producto no lo manejamos, nos dedicamos solo a cuentas de streaming. ¿Te interesa alguna (Netflix, Disney+, HBO Max, Prime Video)?",
]

SPAM_VARIANTS = [
    "Gracias por tu mensaje, en breve te ayudamos 🙌.",
    "¡Recibido! En un momento te atendemos 🙌.",
]


def build_reply(intent: str, text: str, is_delayed: bool = False, name: str | None = None) -> str:
    prefix = "Disculpa la demora en responder 🙏. " if is_delayed else ""
    greet = _greet(name)

    if intent == "ofensivo":
        return random.choice(OFENSIVO_VARIANTS)

    if intent == "reclamo":
        return prefix + random.choice(RECLAMO_VARIANTS)

    if intent == "pedido":
        product = find_available_product(text)
        if not product:
            return f"{prefix}Por ahora no tenemos stock disponible de ese producto. ¿Quieres que te avisemos cuando vuelva a haber?"
        return (
            f"{prefix}Sí tenemos disponible *{product['platform']} - {product['plan_name']}* "
            f"a S/{product['price']}. ¿Confirmamos? Te paso el link de pago por Yape/Plin."
        )

    if intent == "consulta":
        return prefix + random.choice(CONSULTA_VARIANTS).format(greet=greet)

    if intent == "fuera_de_tema":
        return prefix + random.choice(FUERA_DE_TEMA_VARIANTS)

    return prefix + random.choice(SPAM_VARIANTS)


def build_confirmation_reply(product: dict | None) -> str:
    if not product:
        return "Uy, ese producto ya no está disponible 😕. ¿Quieres que te muestre otras opciones?"
    return (
        f"¡Perfecto! Tu pedido de *{product['platform']} - {product['plan_name']}* "
        f"por S/{product['price']} quedó confirmado. Yapea al *{YAPE_NUMBER}* ({YAPE_NAME}) "
        "y mándame la captura del pago para activarte la cuenta 🙌."
    )


def build_rejection_reply() -> str:
    variants = [
        "Sin problema 🙌. Avísame si cambias de opinión o te interesa otra cuenta.",
        "Tranquilo/a, aquí quedo si más adelante te animas 🙌.",
    ]
    return random.choice(variants)


def build_attachment_reply(is_awaiting_confirmation: bool) -> str:
    if is_awaiting_confirmation:
        return "Recibimos tu comprobante ✅, en breve te activamos la cuenta."
    return "Recibimos tu archivo 📎, un asesor lo va a revisar en breve."