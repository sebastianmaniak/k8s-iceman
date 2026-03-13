# WhatsApp kagent Bot

This project is a self-contained WhatsApp bot that connects to a kagent Kubernetes agent over the A2A protocol. It uses Baileys as a linked WhatsApp Web device, so there are no webhooks, no WhatsApp Business API dependencies, and no ingress requirements. The bot runs as a single pod, persists its WhatsApp auth state on a PVC, forwards inbound text messages to kagent, and sends the agent's text response back to WhatsApp.

## Architecture

```text
WhatsApp Cloud (WebSocket via Baileys)
     |
     v
WhatsApp Bot Pod (Node.js + Baileys)
     |
     v  HTTP POST :8083
kagent Controller (A2A router)
     |
     v
Agent Pod (ADK runtime + LLM)
     |
     v  MCP protocol
kagent-tool-server (RemoteMCPServer)
     |
     v
Kubernetes API Server
```

## Project Layout

```text
whatsapp-kagent-bot/
|-- src/
|   |-- index.js
|   |-- a2a-client.js
|   `-- config.js
|-- k8s/
|   |-- 01-agent.yaml
|   |-- 02-pvc.yaml
|   `-- 03-deployment.yaml
|-- Dockerfile
|-- package.json
|-- package-lock.json
|-- .dockerignore
|-- .gitignore
`-- README.md
```

## Prerequisites

- A Kubernetes cluster with kagent installed and healthy
- `kagent-tool-server` running in the `kagent` namespace
- A `ModelConfig` named `default-model-config`
- Storage available for a 1Gi PVC
- Docker or another OCI image builder
- A dedicated WhatsApp number you can pair as a linked device

## Build And Push The Image

Build from the repo root:

```bash
docker build -t ghcr.io/YOUR_ORG/whatsapp-kagent-bot:latest whatsapp-kagent-bot
docker push ghcr.io/YOUR_ORG/whatsapp-kagent-bot:latest
```

If you want an immutable deployment, push a versioned tag as well:

```bash
docker build -t ghcr.io/YOUR_ORG/whatsapp-kagent-bot:v1 whatsapp-kagent-bot
docker push ghcr.io/YOUR_ORG/whatsapp-kagent-bot:v1
```

## Deploy

1. Update the image reference in `k8s/03-deployment.yaml`.
2. Confirm the `KAGENT_A2A_URL` matches your controller route and agent name.
3. Create the PVC:

```bash
kubectl apply -f whatsapp-kagent-bot/k8s/02-pvc.yaml
```

4. Create the agent:

```bash
kubectl apply -f whatsapp-kagent-bot/k8s/01-agent.yaml
```

5. Deploy the bot:

```bash
kubectl apply -f whatsapp-kagent-bot/k8s/03-deployment.yaml
```

## Initial QR Pairing

On the first start, the bot prints a WhatsApp QR code to stdout. Stream the pod logs and scan it from your phone:

```bash
kubectl logs -f deployment/whatsapp-kagent-bot -n kagent
```

In WhatsApp on the phone:

1. Open `Settings`
2. Open `Linked Devices`
3. Tap `Link a Device`
4. Scan the QR code from the pod logs

Once pairing succeeds, the bot stores the Baileys multi-file auth state under `/data/auth` on the PVC. Future pod restarts should reconnect without another QR scan.

## Verify It Works

```bash
kubectl get pods -n kagent -l app=whatsapp-kagent-bot
kubectl logs deployment/whatsapp-kagent-bot -n kagent --tail=100
```

Verification flow:

1. Confirm the deployment is `Running`.
2. Send a text message from an allowed WhatsApp user to the paired number.
3. Watch the bot logs for a forwarded A2A request and a reply.
4. Confirm the WhatsApp chat receives the agent response.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAGENT_A2A_URL` | Yes | n/a | Full kagent A2A endpoint for the WhatsApp agent |
| `AUTH_STATE_DIR` | No | `./auth_state` | Directory where Baileys stores linked-device auth files |
| `ALLOWED_JIDS` | No | empty | Comma-separated allow list of WhatsApp JIDs. If set, other senders are ignored |
| `RESPOND_TO_GROUPS` | No | `false` | Whether the bot should answer in group chats |
| `MENTION_ONLY` | No | `true` | In groups, respond only when the bot is explicitly @mentioned |
| `LOG_LEVEL` | No | `info` | Pino log level |

## Troubleshooting

### Auth State Is Lost After Restarts

- Confirm `k8s/02-pvc.yaml` is applied and bound
- Confirm `k8s/03-deployment.yaml` mounts the PVC at `/data/auth`
- Confirm `AUTH_STATE_DIR=/data/auth`
- Check pod logs for permission errors writing auth files

### Bot Disconnects Repeatedly

- Keep the deployment at exactly one replica
- Keep `strategy.type: Recreate` so two pods never try to use one linked device
- Check whether WhatsApp logged the session out; if it did, the bot clears auth state and waits for a fresh QR scan
- Inspect the bot logs for repeated `connection.update` failures

### A2A Endpoint Is Unreachable

- Verify the `KAGENT_A2A_URL` hostname resolves inside the cluster
- Verify `kagent-controller` is running and listening on port `8083`
- Confirm the agent name in the URL matches `whatsapp-k8s-agent`
- Check for NetworkPolicy or namespace DNS issues

## Security Notes

- Baileys uses an unofficial WhatsApp Web protocol. There is platform risk, including session invalidation or account restrictions.
- Use a dedicated WhatsApp number for this bot, not a personal primary account.
- Restrict senders with `ALLOWED_JIDS` whenever possible.
- Treat the persisted auth state like a credential. Anyone with those files can potentially take over the linked session.
- The bot has indirect access to your cluster through kagent tools. Keep the backing agent prompt and tool approvals aligned with your operational risk tolerance.
