# Telegram Bot (polling) for kagent on maniak-iceman

This app wires a polling Telegram bot to a dedicated kagent Agent.

## Flow

Telegram user -> telegram-bot deployment (polling via Telegram Bot API) -> kagent controller A2A route -> telegram-k8s-agent -> MCP tools / model -> bot reply back to Telegram

## Secrets

- Vault path: `telegram`
- Property: `api_key`
- Synced by External Secrets into `telegram-bot-token` as `TELEGRAM_BOT_TOKEN`

## Models

- `telegram-k8s-agent` uses `default-model-config`
- In this cluster, `default-model-config` currently points to OpenAI `gpt-5.4`

## Test

- Message the bot on Telegram
- Check bot logs:
  - `kubectl -n kagent logs deploy/telegram-bot --tail=100`
- Check agent logs:
  - `kubectl -n kagent logs deploy/telegram-k8s-agent --tail=100`
