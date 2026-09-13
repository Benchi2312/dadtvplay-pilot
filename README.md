# Agente de WhatsApp — self-hosted (Baileys + FastAPI + scikit-learn)

Reemplaza a WaMundo por una integración propia y gratuita.

```
WhatsApp ←→ whatsapp-gateway (Node/Baileys)
                    │ POST /webhook/incoming
                    ▼
            backend (Python/FastAPI)
                    │
        clasifica (scikit-learn) + consulta stock + estado de conversación
                    │
                    ▼
            Supabase (products, conversation_log, conversation_state)
                    │
        responde vía POST {gateway}/send
```

## 1. Base de datos

Corre `supabase_schema.sql` en el SQL Editor de tu proyecto de Supabase.

## 2. Entrenar el clasificador

```bash
cd backend
pip install -r requirements.txt
python train_classifier.py
```

Revisa el reporte de evaluación que imprime — si alguna categoría tiene
precisión/recall muy bajo, probablemente necesita más ejemplos en
`data/training_data.csv`.

## 3. Desplegar el backend (Python) en Render

1. Sube `backend/` a un repo de GitHub.
2. Render → New → Web Service → conecta el repo, Root Directory = `backend`.
3. Build command: `pip install -r requirements.txt && python train_classifier.py`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   (⚠️ NO agregues `--workers` — el debounce vive en memoria de un solo proceso)
5. Variables de entorno:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `GATEWAY_URL` (lo completas después de desplegar el gateway)
   - `GATEWAY_SECRET` (invéntala, debe ser IDÉNTICA en ambos servicios)
   - `YAPE_NUMBER`, `YAPE_NAME` (tus datos reales de pago)
6. Copia la URL pública del backend.

## 4. Desplegar el gateway (Node/Baileys) en Render

1. Mismo repo, otro servicio en Render, Root Directory = `whatsapp-gateway`.
2. Build command: `npm install`
3. Start command: `npm start`
4. Variables de entorno: `GATEWAY_SECRET` (la misma), `BACKEND_URL` (la del paso 3).
5. Copia la URL del gateway y ponla como `GATEWAY_URL` en el backend.

## 5. Vincular WhatsApp

Abre `https://TU-GATEWAY.onrender.com/qr` desde el navegador del celular
de prueba y escanea desde WhatsApp → Dispositivos vinculados.

## ⚠️ Antes de la sustentación

El free tier de Render puede perder la sesión de Baileys si reinicia el
contenedor. Entra a `/qr` 10-15 min antes para confirmar que sigue
conectado, y ten un video de respaldo grabado por si falla el wifi.

## Qué es piloto vs qué falta para producción

- 143 ejemplos de entrenamiento — funcional para demo, un modelo real de
  producción necesitaría cientos/miles de mensajes reales (la tabla
  `conversation_log` ya acumula esos datos para reentrenar después).
- El pago sigue siendo manual (Yape/Plin + captura de comprobante).
- RLS abierto a propósito para simplificar el piloto.