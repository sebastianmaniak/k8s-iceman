"""Telegram bot that forwards messages to a kagent A2A agent."""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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

# Per-user session tracking for conversation continuity
user_sessions: dict[int, str] = {}

# Pending approval tasks: callback_id -> {task_id, session_id, user_id}
pending_approvals: dict[str, dict] = {}


def get_session(user_id: int) -> str:
    if user_id not in user_sessions:
        user_sessions[user_id] = str(uuid.uuid4())
    return user_sessions[user_id]


def _format_parts(parts: list[dict]) -> str | None:
    """Extract readable text from A2A message parts (text and data kinds)."""
    texts = []
    for p in parts:
        kind = p.get("kind", "")
        if kind == "text" and p.get("text"):
            texts.append(p["text"])
        elif kind == "data" and p.get("data"):
            # Structured data part (e.g. tool approval request)
            data = p["data"]
            tool_name = data.get("toolName", "")
            params = data.get("parameters", {})
            if tool_name:
                summary = f"Tool: {tool_name}"
                if params:
                    param_lines = [f"  {k}: {v}" for k, v in params.items()]
                    summary += "\nParameters:\n" + "\n".join(param_lines)
                texts.append(summary)
            else:
                texts.append(json.dumps(data, indent=2))
    return "\n".join(texts) if texts else None


def _extract_text(result: dict) -> str | None:
    """Extract text from an A2A task result (artifacts or status message)."""
    artifacts = result.get("artifacts", [])
    if artifacts:
        parts = artifacts[-1].get("parts", [])
        text = _format_parts(parts)
        if text:
            return text

    status = result.get("status", {})
    if status.get("message", {}).get("parts"):
        text = _format_parts(status["message"]["parts"])
        if text:
            return text

    return None


async def _send_a2a_request(
    task_id: str,
    message_text: str,
    session_id: str,
    context_id: str | None = None,
) -> dict:
    """Send a message to the kagent A2A endpoint and return the raw result."""
    message = {
        "role": "user",
        "parts": [{"kind": "text", "text": message_text}],
    }
    # Include contextId and taskId in the message when replying to an existing task
    if context_id:
        message["contextId"] = context_id
        message["taskId"] = task_id

    payload = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "message/send",
        "params": {
            "id": task_id,
            "sessionId": session_id,
            "message": message,
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

    logger.debug("A2A response: %s", json.dumps(data, indent=2))
    return data.get("result", {})


async def send_a2a_task(message_text: str, session_id: str) -> dict:
    """Send a new task to the kagent A2A endpoint."""
    task_id = str(uuid.uuid4())
    return await _send_a2a_request(task_id, message_text, session_id)


async def send_a2a_reply(
    task_id: str, message_text: str, session_id: str, context_id: str | None = None
) -> dict:
    """Send a reply to an existing task (e.g. for HITL approval)."""
    return await _send_a2a_request(task_id, message_text, session_id, context_id)


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


async def _send_response(thinking_msg, update: Update, text: str) -> None:
    """Send a (possibly long) text response, chunked for Telegram's 4096 char limit."""
    for i in range(0, len(text), 4000):
        chunk = text[i : i + 4000]
        if i == 0:
            await thinking_msg.edit_text(chunk)
        else:
            await update.message.reply_text(chunk)


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
        result = await send_a2a_task(user_text, session_id)
        status = result.get("status", {})
        state = status.get("state", "")
        task_id = result.get("id", "")
        context_id = result.get("contextId", "")

        if state == "input-required":
            # Agent needs human approval (HITL) — show Approve/Reject buttons
            description = _extract_text(result) or "The agent wants to perform an action that requires your approval."
            callback_id = str(uuid.uuid4())[:8]
            pending_approvals[callback_id] = {
                "task_id": task_id,
                "context_id": context_id,
                "session_id": session_id,
                "user_id": user_id,
            }

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Approve", callback_data=f"approve:{callback_id}"),
                    InlineKeyboardButton("Reject", callback_data=f"reject:{callback_id}"),
                ]
            ])

            approval_text = f"Approval required:\n\n{description}"
            # Truncate if too long for Telegram
            if len(approval_text) > 4000:
                approval_text = approval_text[:3997] + "..."
            await thinking_msg.edit_text(approval_text, reply_markup=keyboard)
        else:
            text = _extract_text(result) or "Agent returned no text response."
            await _send_response(thinking_msg, update, text)

    except Exception as e:
        logger.exception("A2A request failed")
        await thinking_msg.edit_text(f"Error contacting agent: {e}")


async def handle_approval_callback(update: Update, _) -> None:
    """Handle Approve/Reject button presses for HITL."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if ":" not in data:
        return

    action, callback_id = data.split(":", 1)
    approval = pending_approvals.pop(callback_id, None)
    if not approval:
        await query.edit_message_text("This approval has expired or was already handled.")
        return

    if update.effective_user.id != approval["user_id"]:
        # Put it back — wrong user
        pending_approvals[callback_id] = approval
        await query.answer("Only the original requester can approve or reject.", show_alert=True)
        return

    task_id = approval["task_id"]
    context_id = approval.get("context_id", "")
    session_id = approval["session_id"]

    if action == "approve":
        await query.edit_message_text("Approved. Processing...")
        reply_text = "approved"
    else:
        await query.edit_message_text("Rejected.")
        reply_text = "rejected"

    try:
        result = await send_a2a_reply(task_id, reply_text, session_id, context_id)
        status = result.get("status", {})
        state = status.get("state", "")

        if state == "input-required":
            # Agent needs another round of approval
            description = _extract_text(result) or "The agent wants to perform another action that requires your approval."
            new_callback_id = str(uuid.uuid4())[:8]
            pending_approvals[new_callback_id] = {
                "task_id": result.get("id", task_id),
                "context_id": result.get("contextId", context_id),
                "session_id": session_id,
                "user_id": approval["user_id"],
            }

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Approve", callback_data=f"approve:{new_callback_id}"),
                    InlineKeyboardButton("Reject", callback_data=f"reject:{new_callback_id}"),
                ]
            ])

            approval_text = f"Approval required:\n\n{description}"
            if len(approval_text) > 4000:
                approval_text = approval_text[:3997] + "..."
            await query.message.reply_text(approval_text, reply_markup=keyboard)
        else:
            text = _extract_text(result)
            if text:
                # Send final response in chunks if needed
                for i in range(0, len(text), 4000):
                    await query.message.reply_text(text[i : i + 4000])
            elif action == "reject":
                pass  # Already showed "Rejected." above
            else:
                await query.message.reply_text("Action completed.")

    except Exception as e:
        logger.exception("A2A approval reply failed")
        await query.message.reply_text(f"Error sending approval: {e}")


def main() -> None:
    logger.info("Starting Telegram kagent bot")
    logger.info("A2A endpoint: %s", KAGENT_A2A_URL)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(handle_approval_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Write health file for k8s probes
    HEALTH_FILE.touch()

    logger.info("Bot started with polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
