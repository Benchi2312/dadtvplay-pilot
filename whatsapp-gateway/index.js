// Gateway de WhatsApp — reemplaza a WaMundo.
//
// Usa Baileys (librería open source, gratuita, self-hosted): no hay
// vendor de por medio, no hay cuenta que "se desactualiza sola", no hay
// marca de agua, no hay que pagar por webhooks.
//
// Responsabilidad única: hablar con WhatsApp. Toda la lógica de negocio
// vive en el backend de Python (FastAPI).

import express from "express";
import qrcode from "qrcode";
import pino from "pino";
import {
  default as makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
} from "@whiskeysockets/baileys";

const PORT = process.env.PORT || 3000;
const GATEWAY_SECRET = process.env.GATEWAY_SECRET;
const BACKEND_URL = process.env.BACKEND_URL;

if (!GATEWAY_SECRET || !BACKEND_URL) {
  console.error("Faltan variables de entorno: GATEWAY_SECRET, BACKEND_URL");
  process.exit(1);
}

const logger = pino({ level: "warn" });

let sock = null;
let lastQR = null;
let connectionStatus = "disconnected";

function extractAttachmentFlag(message) {
  return !!(
    message?.imageMessage ||
    message?.audioMessage ||
    message?.videoMessage ||
    message?.documentMessage ||
    message?.stickerMessage
  );
}

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState("./auth_state");

  sock = makeWASocket({
    auth: state,
    logger,
    printQRInTerminal: false,
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      lastQR = qr;
      connectionStatus = "waiting_for_scan";
      console.log("Nuevo QR disponible — visítalo en GET /qr");
    }

    if (connection === "open") {
      connectionStatus = "connected";
      lastQR = null;
      console.log("Conectado a WhatsApp ✅");
    }

    if (connection === "close") {
      connectionStatus = "disconnected";
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log("Conexión cerrada.", { statusCode, shouldReconnect });
      if (shouldReconnect) {
        startSock();
      } else {
        console.log("Sesión cerrada — hay que volver a escanear el QR (GET /qr).");
      }
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const m of messages) {
      if (m.key.fromMe) continue;
      if (m.key.remoteJid?.endsWith("@g.us")) continue; // ignorar grupos

      const text =
        m.message?.conversation ||
        m.message?.extendedTextMessage?.text ||
        null;

      const hasAttachment = extractAttachmentFlag(m.message);

      if (!text && !hasAttachment) continue; // nada que procesar (ej. reacciones, estados)

      const phoneOrLid = m.key.remoteJid; // Baileys ya resuelve esto bien

      console.log("Mensaje entrante:", { from: phoneOrLid, text, hasAttachment });

      try {
        await fetch(`${BACKEND_URL}/webhook/incoming`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-gateway-secret": GATEWAY_SECRET,
          },
          body: JSON.stringify({
            phone: phoneOrLid,
            message: text,
            attachment: hasAttachment,
            timestamp: Date.now() / 1000,
          }),
        });
      } catch (err) {
        console.error("Error reenviando al backend:", err);
      }
    }
  });
}

startSock();

const app = express();
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true, connectionStatus });
});

app.get("/qr", async (_req, res) => {
  if (connectionStatus === "connected") {
    return res.send("<h2>Ya está conectado ✅ — no hace falta escanear nada.</h2>");
  }
  if (!lastQR) {
    return res.send("<h2>Todavía no hay QR generado, espera unos segundos y recarga.</h2>");
  }
  const dataUrl = await qrcode.toDataURL(lastQR);
  res.send(`<h2>Escanea este código desde WhatsApp → Dispositivos vinculados</h2><img src="${dataUrl}" />`);
});

app.post("/send", async (req, res) => {
  const secret = req.headers["x-gateway-secret"];
  if (secret !== GATEWAY_SECRET) {
    return res.status(401).json({ error: "unauthorized" });
  }
  if (connectionStatus !== "connected") {
    return res.status(503).json({ error: "whatsapp not connected" });
  }

  const { phone, message } = req.body;
  if (!phone || !message) {
    return res.status(400).json({ error: "faltan phone o message" });
  }

  try {
    await sock.sendMessage(phone, { text: message });
    res.json({ ok: true });
  } catch (err) {
    console.error("Error enviando mensaje:", err);
    res.status(500).json({ error: String(err) });
  }
});

app.listen(PORT, () => {
  console.log(`Gateway escuchando en el puerto ${PORT}`);
});