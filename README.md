# Agente de WhatsApp — DadTvPlay (Kapso + FastAPI + scikit-learn)

Bot de atención automática por WhatsApp usando la **Cloud API oficial de
Meta** (vía Kapso como proxy de autenticación) + backend en **FastAPI**
(Python) con clasificador de intención en **scikit-learn**.

```
WhatsApp ←→ Kapso (proxy oficial sobre Meta Cloud API)
                    │ webhook POST /webhook/kapso
                    ▼
            backend (Python/FastAPI) — único servicio
                    │
        clasifica (scikit-learn) + consulta stock + estado de conversación
                    │
                    ▼
            Supabase (products, conversation_log, conversation_state)
                    │
        responde vía POST a Kapso → Meta Cloud API
```

## Por qué Kapso en vez de conexión directa a WhatsApp

Versiones anteriores de este proyecto probaron:
1. **WaMundo** (proveedor no oficial) — descartado por comportamientos no
   documentados y fricción operativa constante.
2. **Baileys self-hosted** (conexión no oficial vía QR) — descartado tras
   sufrir una restricción/logout de la cuenta de WhatsApp a las pocas
   horas de vincularla (riesgo real y documentado de las librerías no
   oficiales, sobre todo en números nuevos).

Kapso es un proxy sobre la **Cloud API oficial de Meta** — mismo canal
sancionado por WhatsApp, cero riesgo de restricción/baneo, con la
ventaja de simplificar la autenticación (una sola API key, sin manejar
tokens de sistema de Meta que expiran).

## 1. Base de datos

Corre `supabase_schema.sql` en el SQL Editor de tu proyecto de Supabase
(si ya lo corriste en una versión anterior, no hace falta repetirlo —
la estructura de tablas no cambió con esta migración).

## 2. Entrenar el clasificador

```bash
cd backend
pip install -r requirements.txt
python train_classifier.py
```

## 3. Desplegar en Render

1. Sube el repo a GitHub (ya hecho si vienes de una versión anterior).
2. Render → New → Blueprint → conecta el repo. Detecta `render.yaml`
   automáticamente (ahora es un solo servicio, `dadtvplay-backend`).
3. Completa las variables que te pide: `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `YAPE_NUMBER`, `YAPE_NAME`,
   `KAPSO_API_KEY`, `KAPSO_PHONE_NUMBER_ID`. `KAPSO_WEBHOOK_SECRET` se
   genera solo.
4. Si vienes de la versión con Baileys: borra manualmente el servicio
   viejo `dadtvplay-gateway` desde el dashboard de Render — ya no se usa
   y el Blueprint no lo elimina automáticamente.

## 4. Registrar el webhook en Kapso

```bash
curl --request POST \
  --url https://api.kapso.ai/platform/v1/whatsapp/phone_numbers/{KAPSO_PHONE_NUMBER_ID}/webhooks \
  --header 'Content-Type: application/json' \
  --header 'X-API-Key: {KAPSO_API_KEY}' \
  --data '{
    "whatsapp_webhook": {
      "kind": "meta",
      "url": "https://{tu-backend}.onrender.com/webhook/kapso?secret={KAPSO_WEBHOOK_SECRET}",
      "active": true
    }
  }'
```

## 5. Probar

Manda un mensaje de WhatsApp al número conectado. Revisa los logs de
Render — deberías ver `"Webhook crudo de Kapso/Meta:"` con el payload
completo, seguido de la respuesta del bot.

## Qué es piloto vs qué falta para producción

- El clasificador tiene ~155 ejemplos de entrenamiento — funcional para
  demo, un modelo de producción real necesitaría más datos (la tabla
  `conversation_log` ya acumula conversaciones reales para reentrenar).
- El pago sigue siendo manual (Yape/Plin + captura de comprobante).
- RLS de Supabase abierto a propósito para simplificar el piloto.
- Costo esperado en Meta Cloud API: $0 — el bot solo responde (nunca
  inicia conversaciones), y las conversaciones iniciadas por el cliente
  son gratis e ilimitadas dentro de la ventana de 24h.