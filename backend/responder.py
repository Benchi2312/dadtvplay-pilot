from config import YAPE_NUMBER, YAPE_NAME
from store import find_available_product


def build_reply(intent: str, text: str, is_delayed: bool = False) -> str:
    prefix = "Disculpa la demora en responder 🙏. " if is_delayed else ""

    if intent == "ofensivo":
        return "Vamos a resolver esto con calma 🙏. Te voy a conectar con un asesor para que te ayude directamente."

    if intent == "reclamo":
        return f"{prefix}Lamentamos el problema con tu cuenta 🙏. Un asesor te va a contactar en breve para ayudarte a resolverlo."

    if intent == "pedido":
        product = find_available_product(text)
        if not product:
            return f"{prefix}Por ahora no tenemos stock disponible de ese producto. ¿Quieres que te avisemos cuando vuelva a haber?"
        return (
            f"{prefix}Sí tenemos disponible *{product['platform']} - {product['plan_name']}* "
            f"a S/{product['price']}. ¿Confirmamos? Te paso el link de pago por Yape/Plin."
        )

    if intent == "consulta":
        return f"{prefix}¡Hola! 👋 Vendemos cuentas de Netflix, Disney+, HBO Max y Prime Video. ¿Cuál te interesa?"

    if intent == "fuera_de_tema":
        return (
            f"{prefix}Por aquí solo vendemos cuentas de streaming (Netflix, Disney+, HBO Max, Prime Video) "
            "— no manejamos ese producto. ¿Te ayudo con alguna cuenta?"
        )

    return f"{prefix}Gracias por tu mensaje, en breve te ayudamos 🙌."


def build_confirmation_reply(product: dict | None) -> str:
    if not product:
        return "Uy, ese producto ya no está disponible 😕. ¿Quieres que te muestre otras opciones?"
    return (
        f"¡Perfecto! Tu pedido de *{product['platform']} - {product['plan_name']}* "
        f"por S/{product['price']} quedó confirmado. Yapea al *{YAPE_NUMBER}* ({YAPE_NAME}) "
        "y mándame la captura del pago para activarte la cuenta 🙌."
    )


def build_rejection_reply() -> str:
    return "Sin problema 🙌. Avísame si cambias de opinión o te interesa otra cuenta."


def build_attachment_reply(is_awaiting_confirmation: bool) -> str:
    if is_awaiting_confirmation:
        return "Recibimos tu comprobante ✅, en breve te activamos la cuenta."
    return "Recibimos tu archivo 📎, un asesor lo va a revisar en breve."