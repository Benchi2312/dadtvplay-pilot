import re
from classifier import normalize

AFFIRMATIVE = re.compile(r"\b(si|sí|confirmo|dale|ok|okay|correcto|de acuerdo|acepto|va)\b")
NEGATIVE = re.compile(r"\b(no|nel|mejor no|cancela|cancelar|olvidalo|olvídalo)\b")


def is_real_affirmative(raw_text: str) -> bool:
    t = normalize(raw_text).strip()
    if "?" in t:
        return False
    return bool(AFFIRMATIVE.search(t))


def is_real_negative(raw_text: str) -> bool:
    t = normalize(raw_text).strip()
    if "?" in t:
        return False
    return bool(NEGATIVE.search(t))