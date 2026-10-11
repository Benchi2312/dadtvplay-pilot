# Agente de WhatsApp — DadTvPlay (WaMundo + FastAPI + scikit-learn)

Bot de atención automática por WhatsApp. Usa la API no oficial de **WaMundo**
como canal, un backend en **FastAPI** (Python) con clasificador de intención
en **scikit-learn**, y **Supabase** para productos y estado de conversación.

```
WhatsApp ←→ WaMundo (API no oficial)
                │ Hook GET /webhook/wamundo-hook?token=...   (solo aviso)
                │            y/o sondeo cada POLL_INTERVAL_SECONDS
                ▼
        backend (Python/FastAPI) — único servicio
                │ consulta GET wa.received y responde con reply_to
                │ clasifica + consulta stock + estado de conversación
                ▼
        Supabase (products, conversation_log, conversation_state, processed_messages)
```

WaMundo no empuja el contenido del mensaje de forma confiable, así que su Hook
funciona solo como disparador: al recibirlo (o en cada ciclo de sondeo), el
backend consulta `wa.received`, filtra la cuenta del bot (`account_unique`),
evita duplicados (`processed_messages.wamundo_id`) y responde al `reply_to`
del mensaje con `POST /send/whatsapp`.

## 1. Base de datos

Corre `supabase_schema.sql` en el SQL Editor de tu proyecto de Supabase. Es
idempotente (todo con `if not exists`), así que se puede correr varias veces.

## 2. Entrenar el clasificador

```bash
cd backend
pip install -r requirements.txt
python train_classifier.py
```

## 3. Variables de entorno

Cópialas desde `backend/.env.example` y complétalas. En Render van con
`sync: false` (excepto `POLL_INTERVAL_SECONDS`, fijo en `30`):

| Variable | Descripción |
| --- | --- |
| `SUPABASE_URL` | URL del proyecto de Supabase. |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key de Supabase. |
| `YAPE_NUMBER` | Número de Yape/Plin que se pasa al cliente. |
| `YAPE_NAME` | Nombre de la cuenta de Yape/Plin. |
| `WAMUNDO_SECRET` | Secret de la API key de WaMundo. |
| `WAMUNDO_ACCOUNT_ID` | Account Unique ID del chip del bot. |
| `WAMUNDO_HOOK_TOKEN` | Token que viaja en la URL del Hook. |
| `POLL_INTERVAL_SECONDS` | Cada cuántos segundos se consulta `wa.received` (30). |

Mientras `WAMUNDO_SECRET` o `WAMUNDO_ACCOUNT_ID` valgan `pendiente`, el bot no
procesa ni responde nada.

## 4. Desplegar en Render

Render → New → Blueprint → conecta el repo. Detecta `render.yaml` (un solo
servicio, `dadtvplay-backend`) y pide las variables de arriba.

## 5. Configurar WaMundo

Pasos: agregar la cuenta (videollamada + QR), copiar el **Account Unique ID** y
crear una **API key** con permiso de enviar WhatsApp y leer chats recibidos.
Luego crear el Hook (Acciones → Hook, origen WhatsApp, evento "Al recibir")
apuntando a `https://dadtvplay-backend.onrender.com/webhook/wamundo-hook?token=EL_TOKEN`.

Antes de activar, verificar que `wa.received` traiga los campos necesarios:

```bash
cd backend
python scripts/check_wamundo.py
```

No re-vincular el WhatsApp después de configurar: el Account Unique ID cambia
y el bot deja de responder.

## Qué es piloto vs qué falta para producción

- El clasificador tiene ~155 ejemplos de entrenamiento — funcional para demo;
  la tabla `conversation_log` acumula conversaciones reales para reentrenar.
- El pago sigue siendo manual (Yape/Plin + captura de comprobante).
- RLS de Supabase abierto a propósito para simplificar el piloto.
- WaMundo no es el canal oficial de WhatsApp: existe riesgo de restricción del
  chip. Ver avisos en `GUIA_DIEGO.md`.
