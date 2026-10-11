# Guía para Diego — conectar el bot a WaMundo

## 1. Qué es esto

El bot de atención automática ya funciona; lo único que cambia es el canal,
que ahora será WaMundo. Diego configura su cuenta de WaMundo y pasa dos
códigos (el Account Unique ID y el secret de la API key). El dueño del repo
carga esas variables en Render y activa el bot.

## 2. Lo que Diego NO hace

- No despliega ni cambia código.
- No toca Render ni Supabase.
- No re-vincula el WhatsApp después de configurar: si se re-vincula, el
  **Account Unique ID cambia** y el bot deja de responder.

## 3. Pasos en su WaMundo, en este orden

1. **Cuentas de WhatsApp → Agregar cuenta**: hay que hacer una videollamada
   con el dueño del chip, que escanea el QR (dura poco y caduca rápido).
2. **CÓDIGO 1 — Account Unique ID**: en Cuentas de WhatsApp, en la cuenta
   nueva, botón de copiar.
3. **CÓDIGO 2 — API key**: Tools → API Keys, crear una clave nueva con
   permisos **solo** de enviar WhatsApp y leer chats recibidos. Copiar el
   secret.
4. **Hook**: Acciones → Hook, origen *WhatsApp*, evento *Al recibir*, y la URL
   `https://dadtvplay-backend.onrender.com/webhook/wamundo-hook?token=EL_TOKEN`
   (el dueño le pasa el token). Si su plan no permite crear el Hook, avisar:
   el bot igual consulta los mensajes cada 30 segundos.

## 4. Cómo pasar los códigos

Por un link que se autodestruye (Bitwarden Send o PrivateBin con expiración)
o en la llamada. **Nunca** por chat normal, capturas ni grupos.

## 5. Comprobación obligatoria antes de activar

Correr el script de verificación (desde la carpeta `backend`, en PowerShell):

```powershell
$env:WAMUNDO_SECRET="su_secret"
$env:WAMUNDO_ACCOUNT_ID="el_account_unique_del_chip_del_bot"
python scripts/check_wamundo.py
```

Si faltan los campos `account_unique` o `reply_to`, **NO activar** y pedir a
soporte de WaMundo que los habiliten.

## 6. Prueba

El dueño escribe **UN** mensaje desde otro celular al chip; luego una ráfaga
de 3 mensajes; luego un pedido completo. Pocas pruebas y espaciadas, para no
gatillar el error 429 ni restricciones en el chip.

## 7. Avisos honestos

- La API key puede leer los mensajes de todas sus cuentas de WaMundo. El bot
  ignora las que no son de la cuenta del bot y no guarda nada de ellas.
- WaMundo no es el canal oficial de WhatsApp: el chip podría restringirse y
  no es 100 % seguro que no afecte a otras cuentas.
- Al terminar la presentación, se rota o borra la API key.

## 8. Si algo falla

| Síntoma | Causa probable |
| --- | --- |
| El bot no responde nada | Variables en `pendiente`, Hook mal creado, o el servicio de Render dormido. |
| En los logs: `account doesn't exist` | `WAMUNDO_ACCOUNT_ID` incorrecto o se re-vinculó el WhatsApp. |
| Responde mensajes de otro número | Falta `account_unique` en `wa.received`; apagar el bot. |
| `429` | Demasiadas consultas; esperar y espaciar las pruebas. |
