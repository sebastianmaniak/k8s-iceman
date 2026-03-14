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

# Per-user contextId for conversation continuity (maps Telegram user_id -> kagent contextId)
user_contexts: dict[int, str] = {}

# Pending approval tasks: callback_id -> {context_id, user_id}
pending_approvals: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_status_parts(result: dict) -> list[dict]:
    """Return the message parts from the status field of an A2A result."""
    return result.get("status", {}).get("message", {}).get("parts", [])


def _parse_adk_confirmation(data: dict) -> dict | None:
    """Parse an adk_request_confirmation DataPart into a structured dict.

    Returns None if the data is not an ADK confirmation.
    Returns: {type: "approval"|"ask_user", tool_name, tool_args, hint, questions, function_call_id}
    """
    if data.get("name") == "adk_request_confirmation":
        function_call_id = data.get("id", "")
        args = data.get("args", {})
        func_call = args.get("originalFunctionCall", {})
        tool_name = func_call.get("name", "")
        tool_args = func_call.get("args", {})
        hint = args.get("toolConfirmation", {}).get("hint", "")

        if tool_name == "ask_user":
            # ask_user tool — extract questions from args
            questions = tool_args.get("questions", [])
            if isinstance(questions, str):
                questions = [{"question": questions}]
            return {"type": "ask_user", "tool_name": tool_name, "questions": questions,
                    "hint": hint, "function_call_id": function_call_id}

        return {"type": "approval", "tool_name": tool_name, "tool_args": tool_args,
                "hint": hint, "function_call_id": function_call_id}

    # Generic A2A format
    if data.get("toolName"):
        return {
            "type": "approval",
            "tool_name": data["toolName"],
            "tool_args": data.get("parameters", {}),
            "hint": "",
            "function_call_id": data.get("id", ""),
        }

    return None


def _classify_input_required(result: dict) -> tuple[str, dict | None]:
    """Classify an input-required response.

    Returns (kind, parsed) where kind is 'approval', 'ask_user', or 'question'.
    """
    for p in _get_status_parts(result):
        if p.get("kind") != "data":
            continue
        parsed = _parse_adk_confirmation(p.get("data", {}))
        if parsed:
            return parsed["type"], parsed
    return "question", None


def _format_approval_text(parsed: dict) -> str:
    """Build a user-friendly approval message from parsed ADK confirmation."""
    tool_name = parsed.get("tool_name", "unknown tool")
    tool_args = parsed.get("tool_args", {})
    hint = parsed.get("hint", "")

    lines = [f"The agent wants to run: {tool_name}"]
    if hint:
        lines.append(hint)

    if tool_args:
        for key, value in tool_args.items():
            val_str = str(value)
            if "\n" in val_str:
                lines.append(f"\n```\n{val_str}```")
            else:
                lines.append(f"  {key}: {val_str}")

    return "\n".join(lines)


def _format_ask_user(parsed: dict) -> tuple[str, list[str]]:
    """Format an ask_user request into a question string and list of choices.

    Returns (question_text, choices).
    """
    questions = parsed.get("questions", [])
    if not questions:
        return "The agent is asking for input.", []

    # Collect all question texts and choices
    q_texts = []
    all_choices = []
    for q in questions:
        if isinstance(q, dict):
            q_text = q.get("question", "")
            choices = q.get("choices", [])
        else:
            q_text = str(q)
            choices = []

        if q_text:
            q_texts.append(q_text)
        if choices:
            all_choices.extend([str(c) for c in choices])

    text = "\n\n".join(q_texts) if q_texts else "The agent is asking for input."
    return text, all_choices


def _extract_text(result: dict) -> str | None:
    """Extract text from an A2A task result (artifacts, history, or status message)."""
    # Check artifacts first (completed responses)
    artifacts = result.get("artifacts", [])
    if artifacts:
        parts = artifacts[-1].get("parts", [])
        texts = [p.get("text", "") for p in parts if p.get("kind") == "text" and p.get("text")]
        if texts:
            return "\n".join(texts)

    # Check history — last agent message with text parts
    for msg in reversed(result.get("history", [])):
        if msg.get("role") != "agent":
            continue
        texts = [p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text" and p.get("text")]
        if texts:
            return "\n".join(texts)

    # Check status message
    for p in _get_status_parts(result):
        kind = p.get("kind", "")
        if kind == "text" and p.get("text"):
            return p["text"]

    return None




# ---------------------------------------------------------------------------
# A2A communication
# ---------------------------------------------------------------------------

async def _send_a2a_request(
    message_text: str,
    context_id: str | None = None,
) -> dict:
    """Send a message to the kagent A2A endpoint and return the raw result."""
    message = {
        "role": "user",
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": message_text}],
    }
    if context_id:
        message["contextId"] = context_id

    payload = {
        "jsonrpc": "2.0",
        "id": message["messageId"],
        "method": "message/send",
        "params": {"message": message},
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
    logger.info("A2A state=%s contextId=%s (sent contextId=%s)",
                result.get("status", {}).get("state"), result.get("contextId"), context_id)
    return result


async def send_a2a_message(message_text: str, context_id: str | None = None) -> dict:
    """Send a message to the kagent A2A endpoint."""
    return await _send_a2a_request(message_text, context_id)


async def send_a2a_confirmation(
    function_call_id: str,
    confirmed: bool,
    context_id: str | None = None,
) -> dict:
    """Send a structured HITL confirmation response via A2A."""
    message_id = str(uuid.uuid4())
    message = {
        "role": "user",
        "kind": "message",
        "messageId": message_id,
        "parts": [
            {
                "kind": "data",
                "data": {
                    "function_response": {
                        "id": function_call_id,
                        "name": "adk_request_confirmation",
                        "response": {"confirmed": confirmed},
                    }
                },
            }
        ],
    }
    if context_id:
        message["contextId"] = context_id

    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {"message": message},
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
    logger.info("A2A confirmation state=%s contextId=%s confirmed=%s",
                result.get("status", {}).get("state"), result.get("contextId"), confirmed)
    return result


# ---------------------------------------------------------------------------
# Telegram command handlers
# ---------------------------------------------------------------------------

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
    uid = update.effective_user.id
    user_contexts.pop(uid, None)
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


# ---------------------------------------------------------------------------
# Response rendering
# ---------------------------------------------------------------------------

async def _edit_or_reply(target, text: str, **kwargs) -> None:
    """Edit the target message if possible, otherwise reply_text."""
    if hasattr(target, "edit_text"):
        await target.edit_text(text, **kwargs)
    else:
        await target.reply_text(text, **kwargs)


async def _send_chunked(target, text: str) -> None:
    """Send text in 4000-char chunks. First chunk edits target if possible."""
    for i in range(0, len(text), 4000):
        chunk = text[i : i + 4000]
        if i == 0 and hasattr(target, "edit_text"):
            await target.edit_text(chunk)
        elif i == 0:
            await target.reply_text(chunk)
        else:
            await target.reply_text(chunk)


async def _handle_input_required(
    result: dict,
    user_id: int,
    reply_target,
) -> None:
    """Handle an input-required A2A result — show approval buttons or a question."""
    context_id = result.get("contextId", "")
    kind, parsed = _classify_input_required(result)

    task_info = {
        "context_id": context_id,
        "user_id": user_id,
    }

    if kind == "approval":
        # Tool approval — show a clean description with Approve / Reject buttons
        description = _format_approval_text(parsed)
        callback_id = str(uuid.uuid4())[:8]
        task_info["function_call_id"] = parsed.get("function_call_id", "")
        pending_approvals[callback_id] = task_info

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{callback_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject:{callback_id}"),
            ]
        ])

        text = description
        if len(text) > 4000:
            text = text[:3997] + "..."
        await _edit_or_reply(reply_target, text, reply_markup=keyboard)

    elif kind == "ask_user" and parsed:
        # ask_user tool — show question with choices or free-text prompt
        question_text, choices = _format_ask_user(parsed)

        if choices:
            callback_id = str(uuid.uuid4())[:8]
            pending_approvals[callback_id] = task_info

            buttons = [
                [InlineKeyboardButton(c, callback_data=f"choice:{callback_id}:{c[:40]}")]
                for c in choices
            ]
            keyboard = InlineKeyboardMarkup(buttons)

            text = question_text
            if len(text) > 4000:
                text = text[:3997] + "..."
            await _edit_or_reply(reply_target, text, reply_markup=keyboard)
        else:
            # Free-text — wait for user's next message
            prompt = f"{question_text}\n\n(Type your answer below)"
            if len(prompt) > 4000:
                prompt = prompt[:3997] + "..."
            await _edit_or_reply(reply_target, prompt)

    else:
        # Generic question fallback
        description = _extract_text(result) or "The agent is asking for input."

        prompt = f"{description}\n\n(Type your answer below)"
        if len(prompt) > 4000:
            prompt = prompt[:3997] + "..."
        await _edit_or_reply(reply_target, prompt)


async def _handle_a2a_result(
    result: dict,
    user_id: int,
    reply_target,
    fallback_text: str = "Agent returned no text response.",
) -> None:
    """Process an A2A result — dispatch to input-required handler or show text."""
    state = result.get("status", {}).get("state", "")

    if state == "input-required":
        await _handle_input_required(result, user_id, reply_target)
    else:
        text = _extract_text(result) or fallback_text
        await _send_chunked(reply_target, text)


# ---------------------------------------------------------------------------
# Message and callback handlers
# ---------------------------------------------------------------------------

async def handle_message(update: Update, _) -> None:
    """Forward user message to kagent A2A and reply with the response."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    user_text = update.message.text
    context_id = user_contexts.get(user_id)

    logger.info("User %s (ctx=%s): %s", user_id, context_id or "new", user_text[:100])
    thinking_msg = await update.message.reply_text("Thinking...")

    try:
        result = await send_a2a_message(user_text, context_id)
        ctx = result.get("contextId")
        if ctx:
            user_contexts[user_id] = ctx
        await _handle_a2a_result(result, user_id, thinking_msg)
    except Exception as e:
        logger.exception("A2A request failed")
        await thinking_msg.edit_text(f"Error contacting agent: {e}")


async def handle_callback(update: Update, _) -> None:
    """Handle inline keyboard button presses (approval and choice selection)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if ":" not in data:
        return

    parts = data.split(":", 2)
    action = parts[0]
    callback_id = parts[1]

    approval = pending_approvals.pop(callback_id, None)
    if not approval:
        await query.edit_message_text("This action has expired or was already handled.")
        return

    if update.effective_user.id != approval["user_id"]:
        pending_approvals[callback_id] = approval
        await query.answer("Only the original requester can respond.", show_alert=True)
        return

    context_id = approval.get("context_id", "")
    user_id = approval["user_id"]

    function_call_id = approval.get("function_call_id", "")

    try:
        if action == "approve":
            await query.edit_message_text("Approved. Processing...")
            if function_call_id:
                result = await send_a2a_confirmation(function_call_id, True, context_id)
            else:
                result = await send_a2a_message("approved", context_id)
        elif action == "reject":
            await query.edit_message_text("Rejected.")
            if function_call_id:
                result = await send_a2a_confirmation(function_call_id, False, context_id)
            else:
                result = await send_a2a_message("rejected", context_id)
        elif action == "choice":
            choice_value = parts[2] if len(parts) > 2 else ""
            await query.edit_message_text(f"Selected: {choice_value}")
            result = await send_a2a_message(choice_value, context_id)
        else:
            return

        ctx = result.get("contextId")
        if ctx:
            user_contexts[user_id] = ctx
        await _handle_a2a_result(
            result, user_id, query.message, fallback_text="Action completed."
        )
    except Exception as e:
        logger.exception("A2A reply failed")
        await query.message.reply_text(f"Error sending response: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting Telegram kagent bot")
    logger.info("A2A endpoint: %s", KAGENT_A2A_URL)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Write health file for k8s probes
    HEALTH_FILE.touch()

    logger.info("Bot started with polling")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
