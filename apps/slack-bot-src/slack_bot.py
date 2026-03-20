"""Slack bot that forwards messages to a kagent A2A agent with HITL support."""

import json
import logging
import os
import re
import uuid
from pathlib import Path

import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("slack-kagent-bot")

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

KAGENT_BASE_URL = os.environ["KAGENT_BASE_URL"]
KAGENT_NAMESPACE = os.getenv("KAGENT_NAMESPACE", "kagent")
KAGENT_AGENT_NAME = os.environ["KAGENT_AGENT_NAME"]
KAGENT_A2A_URL = f"{KAGENT_BASE_URL}/api/a2a/{KAGENT_NAMESPACE}/{KAGENT_AGENT_NAME}/"

HEALTH_FILE = Path("/tmp/bot-healthy")

# Restrict to specific team / channels (optional)
SLACK_TEAM_ID = os.getenv("SLACK_TEAM_ID", "")
SLACK_CHANNEL_IDS = [c.strip() for c in os.getenv("SLACK_CHANNEL_IDS", "").split(",") if c.strip()]

app = App(token=SLACK_BOT_TOKEN)

# Per-thread A2A context: thread_ts -> contextId
thread_contexts: dict[str, str] = {}

# Pending HITL approvals: approval_id -> {task_id, context_id, channel, thread_ts, description}
pending_approvals: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# A2A helpers (mirrors telegram-bot-src patterns)
# ---------------------------------------------------------------------------

def _get_status_parts(result: dict) -> list[dict]:
    return result.get("status", {}).get("message", {}).get("parts", [])


def _parse_adk_confirmation(data: dict) -> dict | None:
    if data.get("name") == "adk_request_confirmation":
        args = data.get("args", {})
        func_call = args.get("originalFunctionCall", {})
        tool_name = func_call.get("name", "")
        tool_args = func_call.get("args", {})
        hint = args.get("toolConfirmation", {}).get("hint", "")

        if tool_name == "ask_user":
            questions = tool_args.get("questions", [])
            if isinstance(questions, str):
                questions = [{"question": questions}]
            return {"type": "ask_user", "tool_name": tool_name, "questions": questions, "hint": hint}

        return {"type": "approval", "tool_name": tool_name, "tool_args": tool_args, "hint": hint}

    if data.get("toolName"):
        return {
            "type": "approval",
            "tool_name": data["toolName"],
            "tool_args": data.get("parameters", {}),
            "hint": "",
        }

    return None


def _classify_input_required(result: dict) -> tuple[str, dict | None]:
    for p in _get_status_parts(result):
        if p.get("kind") != "data":
            continue
        parsed = _parse_adk_confirmation(p.get("data", {}))
        if parsed:
            return parsed["type"], parsed
    return "question", None


def _format_approval_mrkdwn(parsed: dict) -> str:
    tool_name = parsed.get("tool_name", "unknown tool")
    tool_args = parsed.get("tool_args", {})
    hint = parsed.get("hint", "")

    lines = [f"*Tool Approval Required*\nThe agent wants to run: `{tool_name}`"]
    if hint:
        lines.append(hint)

    if tool_args:
        for key, value in tool_args.items():
            val_str = str(value)
            if "\n" in val_str:
                lines.append(f"\n```\n{val_str}\n```")
            else:
                lines.append(f"  `{key}`: {val_str}")

    return "\n".join(lines)


def _format_ask_user(parsed: dict) -> tuple[str, list[str]]:
    questions = parsed.get("questions", [])
    if not questions:
        return "The agent is asking for input.", []

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
    artifacts = result.get("artifacts", [])
    if artifacts:
        parts = artifacts[-1].get("parts", [])
        texts = [p.get("text", "") for p in parts if p.get("kind") == "text" and p.get("text")]
        if texts:
            return "\n".join(texts)

    for msg in reversed(result.get("history", [])):
        if msg.get("role") != "agent":
            continue
        texts = [p.get("text", "") for p in msg.get("parts", []) if p.get("kind") == "text" and p.get("text")]
        if texts:
            return "\n".join(texts)

    for p in _get_status_parts(result):
        if p.get("kind") == "text" and p.get("text"):
            return p["text"]

    return None


# ---------------------------------------------------------------------------
# A2A communication
# ---------------------------------------------------------------------------

def send_a2a_message(message_text: str, context_id: str | None = None, task_id: str | None = None) -> dict:
    message = {
        "role": "user",
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": message_text}],
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id

    payload = {
        "jsonrpc": "2.0",
        "id": message["messageId"],
        "method": "message/send",
        "params": {"message": message, "metadata": {}},
    }

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(KAGENT_A2A_URL, json=payload, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()

    result = data.get("result", {})
    state = result.get("status", {}).get("state")
    logger.info("A2A state=%s contextId=%s taskId=%s", state, result.get("contextId"), result.get("id"))
    return result


def send_a2a_decision(decision: str, context_id: str | None = None, task_id: str | None = None) -> dict:
    decision_type = "approve" if decision == "approve" else "deny"
    decision_label = "Approved" if decision == "approve" else "Denied"

    message_id = str(uuid.uuid4())
    message = {
        "role": "user",
        "kind": "message",
        "messageId": message_id,
        "parts": [
            {"kind": "data", "data": {"decision_type": decision_type}, "metadata": {}},
            {"kind": "text", "text": decision_label},
        ],
    }
    if context_id:
        message["contextId"] = context_id
    if task_id:
        message["taskId"] = task_id

    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {"message": message, "metadata": {}},
    }

    logger.info("Sending HITL decision=%s contextId=%s taskId=%s", decision_type, context_id, task_id)

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(KAGENT_A2A_URL, json=payload, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()

    result = data.get("result", {})
    state = result.get("status", {}).get("state")
    logger.info("A2A decision response state=%s contextId=%s taskId=%s", state, result.get("contextId"), result.get("id"))
    return result


# ---------------------------------------------------------------------------
# Slack message helpers
# ---------------------------------------------------------------------------

def _send_chunked(client, channel: str, thread_ts: str, text: str, update_ts: str | None = None) -> None:
    """Send text in 3000-char chunks. First chunk updates the existing message if update_ts is given."""
    MAX_LEN = 3000
    for i in range(0, len(text), MAX_LEN):
        chunk = text[i: i + MAX_LEN]
        if i == 0 and update_ts:
            client.chat_update(channel=channel, ts=update_ts, text=chunk)
        else:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)


def _post_approval_blocks(client, channel: str, thread_ts: str, approval_id: str, description: str) -> None:
    """Post a Slack message with Approve / Deny buttons."""
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": description[:3000]},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "action_id": "hitl_approve",
                    "value": approval_id,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "action_id": "hitl_deny",
                    "value": approval_id,
                    "style": "danger",
                },
            ],
        },
    ]
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=description[:3000],
        blocks=blocks,
    )


def _post_ask_user_blocks(client, channel: str, thread_ts: str, approval_id: str, question_text: str, choices: list[str]) -> None:
    """Post a Slack message with choice buttons for ask_user."""
    elements = []
    for choice in choices[:5]:  # Slack max 5 buttons per actions block
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": choice[:75]},
            "action_id": f"hitl_choice_{choice[:40]}",
            "value": json.dumps({"approval_id": approval_id, "choice": choice}),
        })

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": question_text[:3000]}},
        {"type": "actions", "elements": elements},
    ]
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=question_text[:3000],
        blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Result handling
# ---------------------------------------------------------------------------

def _handle_input_required(result: dict, client, channel: str, thread_ts: str) -> None:
    context_id = result.get("contextId", "")
    task_id = result.get("id", "")
    kind, parsed = _classify_input_required(result)

    approval_id = str(uuid.uuid4())[:8]
    task_info = {
        "context_id": context_id,
        "task_id": task_id,
        "channel": channel,
        "thread_ts": thread_ts,
    }

    if kind == "approval" and parsed:
        description = _format_approval_mrkdwn(parsed)
        task_info["description"] = description
        pending_approvals[approval_id] = task_info
        _post_approval_blocks(client, channel, thread_ts, approval_id, description)

    elif kind == "ask_user" and parsed:
        question_text, choices = _format_ask_user(parsed)
        task_info["description"] = question_text
        pending_approvals[approval_id] = task_info

        if choices:
            _post_ask_user_blocks(client, channel, thread_ts, approval_id, question_text, choices)
        else:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"{question_text}\n\n_(Reply in this thread to answer)_",
            )

    else:
        description = _extract_text(result) or "The agent is asking for input."
        task_info["description"] = description
        pending_approvals[approval_id] = task_info
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"{description}\n\n_(Reply in this thread to answer)_",
        )


def _handle_a2a_result(result: dict, client, channel: str, thread_ts: str, update_ts: str | None = None) -> None:
    state = result.get("status", {}).get("state", "")

    if state == "input-required":
        # Remove the "Thinking..." message if present
        if update_ts:
            try:
                client.chat_delete(channel=channel, ts=update_ts)
            except Exception:
                pass
        _handle_input_required(result, client, channel, thread_ts)
    else:
        text = _extract_text(result) or "Agent returned no text response."
        _send_chunked(client, channel, thread_ts, text, update_ts=update_ts)


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------

def _get_thread_ts(event: dict) -> str:
    """Return the thread_ts for threading — use existing thread or start one from the message ts."""
    return event.get("thread_ts") or event.get("ts", "")


def _channel_allowed(channel: str) -> bool:
    if not SLACK_CHANNEL_IDS:
        return True
    return channel in SLACK_CHANNEL_IDS


def _find_pending_for_thread(thread_ts: str) -> tuple[str | None, dict | None]:
    """Find a pending approval associated with a thread."""
    for aid, info in pending_approvals.items():
        if info.get("thread_ts") == thread_ts:
            return aid, info
    return None, None


@app.event("app_mention")
def handle_mention(event, client, say):
    """Handle @bot mentions — forward to kagent A2A."""
    channel = event.get("channel", "")
    if not _channel_allowed(channel):
        return

    # Strip the bot mention from the text
    text = event.get("text", "")
    # Remove <@BOTID> prefix
    text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
    if not text:
        return

    thread_ts = _get_thread_ts(event)
    logger.info("Mention in %s (thread=%s): %s", channel, thread_ts, text[:100])

    # Check if this is a reply to a pending approval (free-text answer)
    if event.get("thread_ts"):
        aid, pending = _find_pending_for_thread(event["thread_ts"])
        if pending:
            pending_approvals.pop(aid, None)
            context_id = pending.get("context_id")
            task_id = pending.get("task_id")
            thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Processing...")
            try:
                result = send_a2a_message(text, context_id, task_id)
                ctx = result.get("contextId")
                if ctx:
                    thread_contexts[thread_ts] = ctx
                _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
            except Exception as e:
                logger.exception("A2A reply failed")
                client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error: {e}")
            return

    context_id = thread_contexts.get(thread_ts)
    thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Thinking...")

    try:
        result = send_a2a_message(text, context_id)
        ctx = result.get("contextId")
        if ctx:
            thread_contexts[thread_ts] = ctx
        _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
    except Exception as e:
        logger.exception("A2A request failed")
        client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error contacting agent: {e}")


@app.event("message")
def handle_thread_reply(event, client):
    """Handle threaded replies (without @mention) if there's an active context or pending approval."""
    # Ignore bot messages, message_changed, etc.
    if event.get("subtype"):
        return
    # Only handle threaded replies
    thread_ts = event.get("thread_ts")
    if not thread_ts:
        return

    channel = event.get("channel", "")
    if not _channel_allowed(channel):
        return

    text = event.get("text", "")
    if not text.strip():
        return

    # Check for pending approval first
    aid, pending = _find_pending_for_thread(thread_ts)
    if pending:
        pending_approvals.pop(aid, None)
        context_id = pending.get("context_id")
        task_id = pending.get("task_id")

        # Check for approve/deny keywords
        lower = text.strip().lower()
        thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Processing...")
        try:
            if lower in ("approve", "approved", "yes", "y"):
                result = send_a2a_decision("approve", context_id, task_id)
            elif lower in ("deny", "denied", "reject", "rejected", "no", "n"):
                result = send_a2a_decision("deny", context_id, task_id)
            else:
                result = send_a2a_message(text, context_id, task_id)

            ctx = result.get("contextId")
            if ctx:
                thread_contexts[thread_ts] = ctx
            _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
        except Exception as e:
            logger.exception("A2A reply failed")
            client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error: {e}")
        return

    # If there's an existing context for this thread, continue the conversation
    context_id = thread_contexts.get(thread_ts)
    if not context_id:
        return

    # Strip bot mention if present
    text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
    if not text:
        return

    thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Thinking...")
    try:
        result = send_a2a_message(text, context_id)
        ctx = result.get("contextId")
        if ctx:
            thread_contexts[thread_ts] = ctx
        _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
    except Exception as e:
        logger.exception("A2A request failed")
        client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error contacting agent: {e}")


# ---------------------------------------------------------------------------
# HITL action handlers (button clicks)
# ---------------------------------------------------------------------------

@app.action("hitl_approve")
def handle_approve(ack, body, client):
    ack()
    approval_id = body["actions"][0]["value"]
    pending = pending_approvals.pop(approval_id, None)

    if not pending:
        client.chat_postMessage(
            channel=body["channel"]["id"],
            thread_ts=body["message"].get("thread_ts", body["message"]["ts"]),
            text="This approval has expired or was already handled.",
        )
        return

    channel = pending["channel"]
    thread_ts = pending["thread_ts"]
    context_id = pending.get("context_id")
    task_id = pending.get("task_id")

    # Update the original message — remove buttons, show approved
    try:
        original_text = pending.get("description", "Tool call")
        client.chat_update(
            channel=channel,
            ts=body["message"]["ts"],
            text=f"{original_text}\n\n:white_check_mark: *Approved*",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"{original_text[:2900]}\n\n:white_check_mark: *Approved*"}},
            ],
        )
    except Exception:
        pass

    thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Approved. Processing...")

    try:
        result = send_a2a_decision("approve", context_id, task_id)
        ctx = result.get("contextId")
        if ctx:
            thread_contexts[thread_ts] = ctx
        _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
    except Exception as e:
        logger.exception("A2A approval failed")
        client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error sending approval: {e}")


@app.action("hitl_deny")
def handle_deny(ack, body, client):
    ack()
    approval_id = body["actions"][0]["value"]
    pending = pending_approvals.pop(approval_id, None)

    if not pending:
        client.chat_postMessage(
            channel=body["channel"]["id"],
            thread_ts=body["message"].get("thread_ts", body["message"]["ts"]),
            text="This action has expired or was already handled.",
        )
        return

    channel = pending["channel"]
    thread_ts = pending["thread_ts"]
    context_id = pending.get("context_id")
    task_id = pending.get("task_id")

    # Update the original message — remove buttons, show denied
    try:
        original_text = pending.get("description", "Tool call")
        client.chat_update(
            channel=channel,
            ts=body["message"]["ts"],
            text=f"{original_text}\n\n:x: *Denied*",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"{original_text[:2900]}\n\n:x: *Denied*"}},
            ],
        )
    except Exception:
        pass

    thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Denied. Processing...")

    try:
        result = send_a2a_decision("deny", context_id, task_id)
        ctx = result.get("contextId")
        if ctx:
            thread_contexts[thread_ts] = ctx
        _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
    except Exception as e:
        logger.exception("A2A denial failed")
        client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error sending denial: {e}")


@app.action(re.compile(r"^hitl_choice_"))
def handle_choice(ack, body, client):
    ack()
    action_value = body["actions"][0]["value"]
    try:
        data = json.loads(action_value)
        approval_id = data["approval_id"]
        choice = data["choice"]
    except (json.JSONDecodeError, KeyError):
        return

    pending = pending_approvals.pop(approval_id, None)
    if not pending:
        client.chat_postMessage(
            channel=body["channel"]["id"],
            thread_ts=body["message"].get("thread_ts", body["message"]["ts"]),
            text="This action has expired or was already handled.",
        )
        return

    channel = pending["channel"]
    thread_ts = pending["thread_ts"]
    context_id = pending.get("context_id")
    task_id = pending.get("task_id")

    # Update the original message
    try:
        client.chat_update(
            channel=channel,
            ts=body["message"]["ts"],
            text=f"Selected: {choice}",
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"Selected: *{choice}*"}},
            ],
        )
    except Exception:
        pass

    thinking = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="Processing...")

    try:
        result = send_a2a_message(choice, context_id, task_id)
        ctx = result.get("contextId")
        if ctx:
            thread_contexts[thread_ts] = ctx
        _handle_a2a_result(result, client, channel, thread_ts, update_ts=thinking["ts"])
    except Exception as e:
        logger.exception("A2A choice response failed")
        client.chat_update(channel=channel, ts=thinking["ts"], text=f"Error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Slack kagent bot")
    logger.info("A2A endpoint: %s", KAGENT_A2A_URL)

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)

    # Write health file for k8s probes
    HEALTH_FILE.touch()
    logger.info("Bot started with Socket Mode")

    handler.start()
