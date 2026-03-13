import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);
const FALSE_VALUES = new Set(["0", "false", "no", "off"]);

function readString(name, fallback) {
  const value = process.env[name];
  if (typeof value !== "string" || value.trim() === "") {
    return fallback;
  }

  return value.trim();
}

function readRequiredString(name) {
  const value = process.env[name];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${name} is required.`);
  }

  return value.trim();
}

function readBoolean(name, fallback) {
  const rawValue = process.env[name];
  if (typeof rawValue !== "string" || rawValue.trim() === "") {
    return fallback;
  }

  const normalized = rawValue.trim().toLowerCase();
  if (TRUE_VALUES.has(normalized)) {
    return true;
  }

  if (FALSE_VALUES.has(normalized)) {
    return false;
  }

  throw new Error(
    `${name} must be a boolean-like value (${Array.from(TRUE_VALUES)
      .concat(Array.from(FALSE_VALUES))
      .join(", ")}).`,
  );
}

function readAllowedJids() {
  const rawValue = process.env.ALLOWED_JIDS;
  if (typeof rawValue !== "string" || rawValue.trim() === "") {
    return [];
  }

  return rawValue
    .split(",")
    .map((jid) => jid.trim())
    .filter(Boolean);
}

export async function loadConfig() {
  const a2aUrl = readRequiredString("KAGENT_A2A_URL");

  try {
    new URL(a2aUrl);
  } catch (error) {
    throw new Error(`KAGENT_A2A_URL must be a valid URL. ${error.message}`);
  }

  const authStateDir = resolve(readString("AUTH_STATE_DIR", "./auth_state"));
  await mkdir(authStateDir, { recursive: true });

  return {
    a2aUrl,
    authStateDir,
    allowedJids: readAllowedJids(),
    respondToGroups: readBoolean("RESPOND_TO_GROUPS", false),
    mentionOnly: readBoolean("MENTION_ONLY", true),
    logLevel: readString("LOG_LEVEL", "info").toLowerCase(),
  };
}
