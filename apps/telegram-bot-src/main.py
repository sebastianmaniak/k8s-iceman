"""Telegram bot that forwards messages to a kagent A2A agent."""

import asyncio
import logging
import os
import uuid
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("telegram-kagent-bot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
KAGENT_A2A_URL = os.environ["KAGENT_A2A_URL"]

HEALTH_FILE = Path("/tmp/bot-healthy")


async def send_a2a_task(message_text: str, session_id: str) -> str:
    """Send a message to the kagent A2A endpoint and return the response."""
    task_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "message/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
            },
        },
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            KAGENT_A2A_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    result = data.get("result", {})
    artifacts = result.get("artifacts", [])
    if artifacts:
        parts = artifacts[-1].get("parts", [])
        texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
        if texts:
            return "\n".join(texts)

    status = result.get("status", {})
    if status.get("message", {}).get("parts"):
        parts = status["message"]["parts"]
        texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
        if texts:
            return "\n".join(texts)

    return "Agent returned no text response."


# Per-user session tracking for conversation continuity
user_sessions: dict[int, str] = {}


def get_session(user_id: int) -> str:
    if user_id not in user_sessions:
        user_sessions[user_id] = str(uuid.uuid4())
    return user_sessions[user_id]


async def start_command(update: Update, _) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "Hello! I'm connected to your kagent K8s cluster.\n"
        "Send me any message and I'll forward it to the agent.\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/new - Start a new conversation session\n"
        "/status - Check agent connectivity"
    )


async def new_command(update: Update, _) -> None:
    """Reset the user's A2A session."""
    user_sessions[update.effective_user.id] = str(uuid.uuid4())
    await update.message.reply_text("New session started.")


async def status_command(update: Update, _) -> None:
    """Check connectivity to the kagent A2A endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(KAGENT_A2A_URL.rsplit("/", 3)[0] + "/healthz")
            if resp.status_code == 200:
                await update.message.reply_text("Agent controller is reachable.")
            else:
                await update.message.reply_text(f"Agent controller returned {resp.status_code}.")
    except Exception as e:
        await update.message.reply_text(f"Cannot reach agent controller: {e}")


async def handle_message(update: Update, _) -> None:
    """Forward user message to kagent A2A and reply with the response."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    session_id = get_session(user_id)
    user_text = update.message.text

    logger.info("User %s: %s", user_id, user_text[:100])
    thinking_msg = await update.message.reply_text("Thinking...")

    try:
        response = await send_a2a_task(user_text, session_id)
        # Telegram has a 4096 char limit per message
        for i in range(0, len(response), 4000):
            chunk = response[i : i + 4000]
            if i == 0:
                await thinking_msg.edit_text(chunk)
            else:
                await update.message.reply_text(chunk)
    except Exception as e:
        logger.exception("A2A request failed")
        await thinking_msg.edit_text(f"Error contacting agent: {e}")


def main() -> None:
    logger.info("Starting Telegram kagent bot")
    logger.info("A2A endpoint: %s", KAGENT_A2A_URL)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Write health file for k8s probes
    HEALTH_FILE.touch()

    logger.info("Bot started with polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
