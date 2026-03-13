import { randomUUID } from "node:crypto";

const A2A_TIMEOUT_MS = 120_000;
const NO_TEXT_RESPONSE = "The agent completed the request but returned no text response.";
const TEXT_PART_KIND = "text";

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getByPath(value, path) {
  return path.reduce((currentValue, segment) => {
    if (currentValue === undefined || currentValue === null) {
      return undefined;
    }

    return currentValue[segment];
  }, value);
}

function collectTextsFromParts(parts) {
  if (!Array.isArray(parts)) {
    return [];
  }

  return parts.flatMap((part) => {
    if (!isObject(part)) {
      return [];
    }

    const directText =
      part.kind === TEXT_PART_KIND && typeof part.text === "string" ? part.text.trim() : "";
    if (directText) {
      return [directText];
    }

    if (Array.isArray(part.parts)) {
      return collectTextsFromParts(part.parts);
    }

    return [];
  });
}

function collectTextsFromNode(node, depth = 0) {
  if (depth > 6) {
    return [];
  }

  if (Array.isArray(node)) {
    return node.flatMap((item) => collectTextsFromNode(item, depth + 1));
  }

  if (!isObject(node)) {
    return [];
  }

  if (Array.isArray(node.parts)) {
    const texts = collectTextsFromParts(node.parts);
    if (texts.length > 0) {
      return texts;
    }
  }

  return Object.values(node).flatMap((value) => collectTextsFromNode(value, depth + 1));
}

function dedupeTexts(texts) {
  const uniqueTexts = [];
  const seen = new Set();

  for (const text of texts) {
    if (!text || seen.has(text)) {
      continue;
    }

    seen.add(text);
    uniqueTexts.push(text);
  }

  return uniqueTexts;
}

function extractTextResponse(payload) {
  const candidatePaths = [
    ["result", "message", "parts"],
    ["result", "status", "message", "parts"],
    ["result", "artifacts"],
    ["result", "messages"],
  ];

  const texts = candidatePaths.flatMap((path) => {
    const candidate = getByPath(payload, path);
    if (Array.isArray(candidate) && path[path.length - 1] === "parts") {
      return collectTextsFromParts(candidate);
    }

    return collectTextsFromNode(candidate);
  });

  if (texts.length > 0) {
    return dedupeTexts(texts).join("\n\n");
  }

  const fallback = collectTextsFromNode(payload.result);
  if (fallback.length > 0) {
    return dedupeTexts(fallback).join("\n\n");
  }

  return NO_TEXT_RESPONSE;
}

function extractContextId(payload) {
  const candidatePaths = [
    ["result", "contextId"],
    ["result", "message", "contextId"],
    ["result", "status", "contextId"],
    ["result", "status", "message", "contextId"],
  ];

  for (const path of candidatePaths) {
    const value = getByPath(payload, path);
    if (typeof value === "string" && value.trim() !== "") {
      return value;
    }
  }

  return null;
}

export class A2ARequestError extends Error {
  constructor(message, { cause, details } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = "A2ARequestError";
    this.details = details;
  }
}

export async function sendMessageToA2A({ a2aUrl, contextId, text }) {
  const requestId = randomUUID();
  const messageId = randomUUID();
  const payload = {
    jsonrpc: "2.0",
    id: requestId,
    method: "message/send",
    params: {
      message: {
        role: "user",
        parts: [{ kind: TEXT_PART_KIND, text }],
        messageId,
        contextId,
      },
      configuration: {
        acceptedOutputModes: ["text"],
      },
    },
  };

  let response;
  try {
    response = await fetch(a2aUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(A2A_TIMEOUT_MS),
    });
  } catch (error) {
    if (error.name === "TimeoutError" || error.name === "AbortError") {
      throw new A2ARequestError(
        "The Kubernetes agent timed out after 120 seconds. Try a narrower question and send it again.",
        { cause: error },
      );
    }

    throw new A2ARequestError(
      "I could not reach the Kubernetes agent right now. Try again in a moment.",
      { cause: error },
    );
  }

  let responseBody;
  const rawBody = await response.text();
  try {
    responseBody = rawBody ? JSON.parse(rawBody) : {};
  } catch (error) {
    throw new A2ARequestError("The Kubernetes agent returned invalid JSON.", {
      cause: error,
      details: rawBody.slice(0, 500),
    });
  }

  if (!response.ok) {
    throw new A2ARequestError(
      `The Kubernetes agent returned HTTP ${response.status}. Try again in a moment.`,
      { details: responseBody },
    );
  }

  if (isObject(responseBody.error)) {
    const message =
      typeof responseBody.error.message === "string"
        ? responseBody.error.message
        : "Unknown JSON-RPC error";
    throw new A2ARequestError(`The Kubernetes agent reported an error: ${message}`, {
      details: responseBody.error,
    });
  }

  return {
    contextId: extractContextId(responseBody) ?? contextId,
    text: extractTextResponse(responseBody),
  };
}
