import { randomUUID } from "node:crypto";
import { mkdir, rm } from "node:fs/promises";

import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  jidNormalizedUser,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode-terminal";

import { A2ARequestError, sendMessageToA2A } from "./a2a-client.js";
import { loadConfig } from "./config.js";

const MAX_MESSAGE_LENGTH = 4000;
const RECONNECT_DELAY_MS = 5_000;
const CONTEXT_IDS = new Map();

let config;
let logger;
let allowedJids = new Set();
let botJid = null;
let reconnectTimer = null;
let currentSocket = null;
let socketGeneration = 0;

function normalizeJid(jid) {
  if (!jid || typeof jid !== "string") {
    return null;
  }

  try {
    return jidNormalizedUser(jid);
  } catch {
    return jid.trim();
  }
}

function isGroupJid(jid) {
  return typeof jid === "string" && jid.endsWith("@g.us");
}

function getDisconnectStatusCode(error) {
  return (
    error?.output?.statusCode ??
    error?.data?.statusCode ??
    error?.statusCode ??
    error?.cause?.output?.statusCode ??
    null
  );
}

function getOrCreateContextId(chatJid) {
  let contextId = CONTEXT_IDS.get(chatJid);
  if (!contextId) {
    contextId = randomUUID();
    CONTEXT_IDS.set(chatJid, contextId);
  }

  return contextId;
}

function chunkText(text, chunkSize = MAX_MESSAGE_LENGTH) {
  if (text.length <= chunkSize) {
    return [text];
  }

  const chunks = [];
  let remaining = text.trim();

  while (remaining.length > 0) {
    if (remaining.length <= chunkSize) {
      chunks.push(remaining);
      break;
    }

    const candidates = [
      remaining.lastIndexOf("\n\n", chunkSize),
      remaining.lastIndexOf("\n", chunkSize),
      remaining.lastIndexOf(" ", chunkSize),
    ];
    const splitIndex = candidates.find((index) => index >= Math.floor(chunkSize * 0.5)) ?? chunkSize;
    chunks.push(remaining.slice(0, splitIndex).trim());
    remaining = remaining.slice(splitIndex).trimStart();
  }

  return chunks.filter(Boolean);
}

function unwrapMessageContent(message) {
  let current = message;

  while (current && typeof current === "object") {
    if (current.ephemeralMessage?.message) {
      current = current.ephemeralMessage.message;
      continue;
    }

    if (current.viewOnceMessage?.message) {
      current = current.viewOnceMessage.message;
      continue;
    }

    if (current.viewOnceMessageV2?.message) {
      current = current.viewOnceMessageV2.message;
      continue;
    }

    if (current.viewOnceMessageV2Extension?.message) {
      current = current.viewOnceMessageV2Extension.message;
      continue;
    }

    if (current.editedMessage?.message) {
      current = current.editedMessage.message;
      continue;
    }

    if (current.documentWithCaptionMessage?.message) {
      current = current.documentWithCaptionMessage.message;
      continue;
    }

    return current;
  }

  return null;
}

function extractTextMessage(content) {
  if (!content || typeof content !== "object") {
    return null;
  }

  if (typeof content.conversation === "string" && content.conversation.trim() !== "") {
    return content.conversation.trim();
  }

  if (
    typeof content.extendedTextMessage?.text === "string" &&
    content.extendedTextMessage.text.trim() !== ""
  ) {
    return content.extendedTextMessage.text.trim();
  }

  return null;
}

function extractMentionedJids(content) {
  const mentioned = new Set();
  const candidateLists = [
    content?.extendedTextMessage?.contextInfo?.mentionedJid,
    content?.messageContextInfo?.mentionedJid,
  ];

  for (const candidateList of candidateLists) {
    if (!Array.isArray(candidateList)) {
      continue;
    }

    for (const jid of candidateList) {
      const normalized = normalizeJid(jid);
      if (normalized) {
        mentioned.add(normalized);
      }
    }
  }

  return mentioned;
}

function isSupportedIncomingMessage(message) {
  if (!message?.message) {
    return false;
  }

  if (message.key?.fromMe || message.key?.remoteJid === "status@broadcast") {
    return false;
  }

  if (message.messageStubType) {
    return false;
  }

  const content = unwrapMessageContent(message.message);
  if (!content) {
    return false;
  }

  if (content.protocolMessage || content.reactionMessage) {
    return false;
  }

  return extractTextMessage(content) !== null;
}

async function clearAuthStateDirectory() {
  await rm(config.authStateDir, { recursive: true, force: true });
  await mkdir(config.authStateDir, { recursive: true });
}

function scheduleReconnect(reason) {
  if (reconnectTimer) {
    return;
  }

  logger.info({ reason, delayMs: RECONNECT_DELAY_MS }, "Scheduling WhatsApp reconnect");
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startSocket().catch((error) => {
      logger.error({ err: error }, "Failed to restart WhatsApp socket");
      scheduleReconnect("restart-failed");
    });
  }, RECONNECT_DELAY_MS);
}

async function safeSendPresence(sock, state, jid) {
  try {
    await sock.sendPresenceUpdate(state, jid);
  } catch (error) {
    logger.debug({ err: error, jid, state }, "Presence update failed");
  }
}

async function replyWithChunks(sock, message, text) {
  for (const chunk of chunkText(text)) {
    await sock.sendMessage(message.key.remoteJid, { text: chunk }, { quoted: message });
  }
}

async function handleIncomingMessage(sock, message) {
  if (!isSupportedIncomingMessage(message)) {
    return;
  }

  const remoteJid = normalizeJid(message.key.remoteJid);
  const senderJid = normalizeJid(message.key.participant || message.key.remoteJid);
  const isGroup = isGroupJid(remoteJid);

  if (!remoteJid || !senderJid) {
    return;
  }

  if (
    allowedJids.size > 0 &&
    !allowedJids.has(senderJid) &&
    !allowedJids.has(remoteJid)
  ) {
    logger.debug({ senderJid, remoteJid }, "Ignoring message from JID not in allow list");
    return;
  }

  if (isGroup && !config.respondToGroups) {
    logger.debug({ remoteJid }, "Ignoring group message because RESPOND_TO_GROUPS=false");
    return;
  }

  const content = unwrapMessageContent(message.message);
  const text = extractTextMessage(content);
  if (!text) {
    return;
  }

  if (isGroup && config.mentionOnly) {
    const mentionedJids = extractMentionedJids(content);
    if (!botJid || !mentionedJids.has(botJid)) {
      logger.debug({ remoteJid, botJid }, "Ignoring group message without bot mention");
      return;
    }
  }

  const contextId = getOrCreateContextId(remoteJid);
  logger.info(
    {
      chatJid: remoteJid,
      contextId,
      isGroup,
      senderJid,
    },
    "Forwarding WhatsApp text message to kagent",
  );

  await safeSendPresence(sock, "composing", remoteJid);

  try {
    const response = await sendMessageToA2A({
      a2aUrl: config.a2aUrl,
      contextId,
      text,
    });

    CONTEXT_IDS.set(remoteJid, response.contextId);
    await replyWithChunks(sock, message, response.text);
  } catch (error) {
    const friendlyMessage =
      error instanceof A2ARequestError
        ? error.message
        : "I hit a temporary error while talking to the Kubernetes agent. Please try again.";

    logger.error(
      {
        err: error,
        chatJid: remoteJid,
        details: error.details,
      },
      "Failed to process WhatsApp message",
    );

    await replyWithChunks(sock, message, friendlyMessage);
  } finally {
    await safeSendPresence(sock, "paused", remoteJid);
  }
}

async function handleConnectionUpdate(sock, generation, update) {
  const { connection, lastDisconnect, qr } = update;

  if (generation !== socketGeneration) {
    return;
  }

  if (qr) {
    logger.info("Scan the QR code below from WhatsApp > Linked Devices.");
    qrcode.generate(qr, { small: true });
  }

  if (sock.user?.id) {
    botJid = normalizeJid(sock.user.id);
  }

  if (connection === "open") {
    botJid = normalizeJid(sock.user?.id);
    logger.info({ botJid }, "WhatsApp connection established");
    return;
  }

  if (connection !== "close") {
    return;
  }

  const statusCode = getDisconnectStatusCode(lastDisconnect?.error);
  const loggedOut = statusCode === DisconnectReason.loggedOut;

  logger.warn({ statusCode, loggedOut }, "WhatsApp connection closed");

  if (loggedOut) {
    botJid = null;
    await clearAuthStateDirectory();
    logger.warn("Persisted auth state cleared after logout; waiting for a fresh QR scan");
  }

  scheduleReconnect(loggedOut ? "logged-out" : "connection-closed");
}

async function startSocket() {
  const generation = ++socketGeneration;
  const { state, saveCreds } = await useMultiFileAuthState(config.authStateDir);

  let version;
  try {
    const latestVersion = await fetchLatestBaileysVersion();
    version = latestVersion.version;
    logger.info({ version, isLatest: latestVersion.isLatest }, "Loaded latest Baileys WA version");
  } catch (error) {
    logger.warn({ err: error }, "Could not fetch latest Baileys WA version; using library defaults");
  }

  const socketLogger = logger.child({ component: "baileys" });
  const sock = makeWASocket({
    auth: state,
    browser: ["Kagent WhatsApp Bot", "Chrome", "22.0.0"],
    defaultQueryTimeoutMs: 120_000,
    logger: socketLogger,
    markOnlineOnConnect: false,
    printQRInTerminal: false,
    shouldIgnoreJid: (jid) => jid === "status@broadcast",
    syncFullHistory: false,
    version,
  });

  currentSocket = sock;

  sock.ev.on("creds.update", saveCreds);
  sock.ev.on("connection.update", (update) => {
    handleConnectionUpdate(sock, generation, update).catch((error) => {
      logger.error({ err: error }, "Connection update handler failed");
      scheduleReconnect("connection-handler-error");
    });
  });
  sock.ev.on("messages.upsert", (event) => {
    if (generation !== socketGeneration || event.type !== "notify") {
      return;
    }

    for (const message of event.messages) {
      handleIncomingMessage(sock, message).catch((error) => {
        logger.error({ err: error }, "Message handler failed");
      });
    }
  });
}

async function main() {
  config = await loadConfig();
  logger = pino({ level: config.logLevel });

  allowedJids = new Set(config.allowedJids.map((jid) => normalizeJid(jid)).filter(Boolean));

  logger.info(
    {
      a2aUrl: config.a2aUrl,
      authStateDir: config.authStateDir,
      allowedJids: Array.from(allowedJids),
      mentionOnly: config.mentionOnly,
      respondToGroups: config.respondToGroups,
    },
    "Starting WhatsApp kagent bot",
  );

  await startSocket();
}

process.on("SIGINT", () => {
  logger?.info("Received SIGINT, exiting");
  currentSocket?.end?.(new Error("SIGINT"));
  process.exit(0);
});

process.on("SIGTERM", () => {
  logger?.info("Received SIGTERM, exiting");
  currentSocket?.end?.(new Error("SIGTERM"));
  process.exit(0);
});

process.on("unhandledRejection", (error) => {
  logger?.error({ err: error }, "Unhandled promise rejection");
});

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
