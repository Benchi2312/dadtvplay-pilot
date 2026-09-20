import unicodedata
import joblib

_model = joblib.load("model.pkl")

KNOWN_PLATFORMS = ["netflix", "disney", "hbo", "max", "prime", "spotify", "star"]


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def classify_intent(text: str) -> dict:
    norm = normalize(text)
    intent = _model.predict([norm])[0]
    proba = float(_model.predict_proba([norm]).max())

    mentions_platform = any(p in norm for p in KNOWN_PLATFORMS)
    # Regla estructural, sin importar la confianza del modelo: si se nombra
    # una plataforma de tu catálogo, nunca puede ser "fuera de tema" ni
    # "spam" — como mínimo es una consulta sobre ese producto.
    # NO reforzamos consulta->pedido: una pregunta real sobre una plataforma
    # ("qué planes tiene netflix?") debe quedarse en consulta, no forzarse a
    # pedido solo por mencionar el nombre.
    if mentions_platform and intent in ("spam", "fuera_de_tema"):
        intent = "pedido"

    return {"intent": intent, "confidence": proba}