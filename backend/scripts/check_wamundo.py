"""Verifica que wa.received de WaMundo trae los campos que el bot necesita.

Uso (PowerShell, desde la carpeta backend):
  $env:WAMUNDO_SECRET="tu_secret"
  $env:WAMUNDO_ACCOUNT_ID="el_account_unique_del_chip_del_bot"   # opcional
  python scripts/check_wamundo.py

No imprime el secret ni el contenido de los mensajes.
"""
import os
import sys

import httpx

secret = os.environ.get("WAMUNDO_SECRET")
if not secret:
    print("Falta la variable de entorno WAMUNDO_SECRET")
    sys.exit(1)
expected_account = os.environ.get("WAMUNDO_ACCOUNT_ID")

r = httpx.get(
    "https://wamundo.com/api/get/wa.received",
    params={"secret": secret, "limit": "10"},
    timeout=20,
)
print("HTTP:", r.status_code)
if r.status_code == 429:
    print("429: demasiadas consultas. Espera un minuto y vuelve a correrlo.")
    sys.exit(1)
try:
    body = r.json()
except Exception:
    print("La respuesta no es JSON.")
    sys.exit(1)

print("status del body:", body.get("status"), "|", body.get("message"))
data = body.get("data")
if not isinstance(data, list):
    print("El campo data no es una lista.")
    sys.exit(1)

print("Mensajes devueltos:", len(data))
for field in ["id", "account_unique", "reply_to", "message", "created"]:
    n = sum(1 for m in data if m.get(field) is not None)
    print(f"  campo {field}: presente en {n}/{len(data)}")

accounts = {}
for m in data:
    a = m.get("account_unique")
    accounts[a] = accounts.get(a, 0) + 1
print("Cuentas distintas en la respuesta:")
for a, n in accounts.items():
    label = "(SIN account_unique)" if a is None else f"{str(a)[:6]}...{str(a)[-4:]}"
    mark = "  <- ES LA CUENTA DEL BOT" if expected_account and a == expected_account else ""
    print(f"  {label}: {n} mensajes{mark}")

if not data:
    print("\nNo hay mensajes todavia: escribe uno al chip del bot y vuelve a correr el script.")
else:
    missing = [f for f in ["account_unique", "reply_to"] if any(m.get(f) is None for m in data)]
    if missing:
        print("\nATENCION: faltan campos:", missing)
        print("NO activar el bot. Pedir a soporte de WaMundo que los habiliten.")
    else:
        print("\nOK: los campos necesarios estan presentes.")
