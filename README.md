# k8s-iceman

GitOps-managed Kubernetes cluster running on Talos Linux, powered by Argo CD.

Deploys open-source tools from the Solo.io ecosystem: **Istio Ambient Mesh**, **kagent**, and **Agentgateway**. Secrets are managed by **HashiCorp Vault OSS** + **External Secrets Operator**. Observability via **OpenTelemetry Collector**.

## Table of Contents

- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Component Versions](#component-versions)
- [Sync Wave Order](#sync-wave-order)
- [Quick Start](#quick-start)
- [Secrets Management](#secrets-management)
- [F5 BIG-IP Load Balancing](#f5-bigip-load-balancing)
- [Slack Bot (agentevals)](#slack-bot-agentevals)
- [Telegram Bot](#telegram-bot)
- [F5 BIG-IP Agent](#f5-bigip-agent)
- [FortiGate Agent](#fortigate-agent)
- [Moat Sandbox Coder Agent](#moat-sandbox-coder-agent)
- [GitHub Agent](#github-agent)
- [kagent Examples](#kagent-examples)
- [CI/CD](#cicd)
- [Making Changes (GitOps Workflow)](#making-changes-gitops-workflow)

## Architecture

```
GitHub Repo (this repo)          Argo CD                    Talos k8s Cluster
┌──────────────────────┐    ┌──────────────┐    ┌─────────────────────────────┐
│  apps/               │───>│  Root App    │───>│  vault                      │
│    vault.yaml        │    │  (App of     │    │    HashiCorp Vault OSS      │
│    external-secrets  │    │   Apps)      │    │                             │
│    istio-*.yaml      │    │              │    │  external-secrets           │
│    kagent-*.yaml     │    │  Syncs git   │    │    External Secrets Op.     │
│    agentgateway-*    │    │  -> cluster  │    │                             │
│    vault-config.yaml │    │              │    │  istio-system               │
│                      │    │  Auto-heal   │    │    istiod (ambient)         │
│  helm-values/        │    │  Auto-prune  │    │    istio-cni + ztunnel      │
│    vault/values.yaml │    └──────────────┘    │                             │
│    kagent/values.yaml│           │            │  kagent                     │
│    ...               │           │            │    kagent (AI agents)       │
│                      │           │            │    telegram-bot ──> A2A     │
│  manifests/          │           │            │                             │
│    vault-config/     │───────────┘            │  agentgateway-system        │
│      ClusterSecret   │                        │    agentgateway (AI proxy)  │
│      ExternalSecret  │  Vault ──> ESO ──> K8s │                             │
│                      │    Secrets flow        │  longhorn-system (existing) │
│  .github/workflows/  │                        │  f5-wrapper pod             │
│    telegram-bot CI   │──> Docker Hub          │    FastAPI ──> F5 iControl  │
│    f5-wrapper CI     │    ├ sebbycorp/         └─────────────────────────────┘
└──────────────────────┘    │  telegram-kagent-bot
                            └ sebbycorp/f5-wrapper

Telegram User ──> Telegram API ──> telegram-bot pod ──(A2A)──> kagent-controller
                                       │                            │
                                   Approve/Reject              Tool execution
                                   ask_user replies            (K8s, F5, etc.)

kagent UI/CLI ──> kagent-controller ──(HTTP/OpenAPI)──> f5-wrapper pod ──> F5 BIG-IP
```

## Repository Structure

```
k8s-iceman/
├── bootstrap/                        # One-time cluster bootstrap
│   ├── install.sh                   # Installs Argo CD + deploys root app
│   ├── vault-init.sh               # Post-deploy: init/unseal Vault + store secrets
│   └── root-app.yaml               # App of Apps - manages everything in apps/
├── apps/                             # Argo CD Application manifests
│   ├── vault.yaml                   # [Wave 0] HashiCorp Vault OSS
│   ├── external-secrets.yaml        # [Wave 0] External Secrets Operator
│   ├── istio-base.yaml              # [Wave 1] Istio CRDs
│   ├── istiod.yaml                  # [Wave 2] Istio control plane (ambient)
│   ├── istio-cni.yaml               # [Wave 3] Istio CNI node agent
│   ├── ztunnel.yaml                 # [Wave 3] Istio zero-trust tunnel
│   ├── kagent-crds.yaml             # [Wave 4] kagent CRDs
│   ├── agentgateway-crds.yaml       # [Wave 4] agentgateway CRDs
│   ├── kagent.yaml                  # [Wave 5] kagent AI agent framework
│   ├── agentgateway.yaml            # [Wave 5] agentgateway AI proxy
│   ├── vault-config.yaml            # [Wave 6] SecretStore + ExternalSecrets
│   ├── kagent-examples.yaml         # [Wave 6] kagent example agents + shared resources
│   ├── agentgateway-config.yaml     # [Wave 7] Agentgateway Langfuse tracing config
│   ├── github-agent.yaml            # [Wave 7] GitHub agent manifests
│   ├── otel-collector.yaml          # [Wave 7] OpenTelemetry Collector
│   ├── service-nodeports.yaml       # [Wave 7] NodePort services for F5 BIG-IP
│   ├── slack-bot.yaml               # [Wave 7] Slack bot agent + deployment
│   └── telegram-bot.yaml            # [Wave 7] Telegram bot agent + deployment
├── apps/telegram-bot-src/            # Telegram bot source code
│   ├── main.py                      # Bot implementation (A2A + HITL)
│   ├── requirements.txt             # Python dependencies
│   └── Dockerfile                   # Container image build
├── apps/slack-bot-src/               # Slack bot source code
│   ├── slack_bot.py                 # Bot implementation (A2A + HITL)
│   ├── requirements.txt             # Python dependencies
│   ├── build.sh                     # Build helper script
│   └── Dockerfile                   # Container image build
├── apps/f5-wrapper/                  # F5 BIG-IP API wrapper service
│   ├── app/main.py                  # FastAPI entrypoint
│   ├── app/config.py                # Settings from env vars
│   ├── app/auth.py                  # F5 token management
│   ├── app/utils/f5_client.py       # HTTP client for iControl REST
│   ├── app/routers/                 # API routers (pools, nodes, VS, etc.)
│   ├── requirements.txt             # Python dependencies
│   └── Dockerfile                   # Container image build
├── apps/fortigate-wrapper-src/       # FortiGate MCP wrapper source code
│   ├── main.py                      # MCP server implementation
│   ├── requirements.txt             # Python dependencies
│   └── Dockerfile                   # Container image build
├── helm-values/                      # Helm value overrides (GitOps managed)
│   ├── vault/values.yaml            # Standalone mode, Longhorn storage
│   ├── external-secrets/values.yaml
│   ├── istio-base/values.yaml
│   ├── istiod/values.yaml           # ambient profile enabled
│   ├── istio-cni/values.yaml        # ambient profile enabled
│   ├── ztunnel/values.yaml
│   ├── kagent-crds/values.yaml
│   ├── kagent/values.yaml           # References Vault-managed secret
│   ├── agentgateway-crds/values.yaml
│   ├── agentgateway/values.yaml
│   └── otel-collector/values.yaml   # OpenTelemetry Collector config
├── manifests/                        # Raw Kubernetes manifests
│   ├── vault-config/
│   │   ├── cluster-secret-store.yaml          # ClusterSecretStore -> Vault
│   │   ├── external-secret-kagent.yaml        # ExternalSecrets for LLM API keys
│   │   ├── external-secret-langfuse-otel.yaml # Langfuse/OTel secrets
│   │   └── external-secret-agentgateway-langfuse-otel.yaml
│   ├── agentgateway-config/
│   │   └── langfuse-tracing.yaml    # Agentgateway Langfuse tracing setup
│   ├── github-agent/
│   │   ├── 01-external-secret.yaml  # GitHub token from Vault
│   │   ├── 02-github-mcp.yaml      # GitHub MCP server
│   │   └── 03-agent.yaml           # GitHub agent (agentevals docs)
│   ├── kagent-examples/             # Example kagent agents and patterns
│   │   ├── 00-shared-resources.yaml # Shared ModelConfigs (openai-embed, etc.)
│   │   ├── context-management/      # Context compaction examples
│   │   ├── f5-agent/bigip/          # F5 BIG-IP agent + wrapper deployment
│   │   ├── fortigate-agent/         # FortiGate firewall agent + MCP server
│   │   ├── git-skills/              # Git-based skill loading examples
│   │   ├── human-in-the-loop/       # HITL approval examples
│   │   ├── memory/                  # Agent memory examples
│   │   ├── moat-agent/              # Moat sandbox coder agent + MCP server
│   │   ├── multi-runtime/           # Go + Python runtime examples
│   │   ├── prompt-templates/        # Agent prompt template examples
│   │   ├── telegram-f5-bot/         # Telegram bot for F5 agent
│   │   ├── telegram-forti-bot/      # Telegram bot for FortiGate agent
│   │   ├── tools/                   # Tool configuration examples
│   │   └── vault-agent/             # Vault management agent
│   ├── service-nodeports/           # NodePort services for F5 BIG-IP
│   │   ├── argocd-nodeport.yaml
│   │   ├── vault-nodeport.yaml
│   │   └── kagent-nodeport.yaml
│   ├── slack-bot/
│   │   ├── 01-external-secret.yaml  # Pulls Slack credentials from Vault
│   │   ├── 02-slack-mcp.yaml       # Slack MCP server (stdio transport)
│   │   ├── 03-agent.yaml           # agentevals-agent with Slack + GitHub tools
│   │   └── 04-deployment.yaml      # Slack bot Deployment (Socket Mode)
│   └── telegram-bot/
│       ├── external-secret.yaml     # Telegram token from Vault
│       ├── serviceaccount.yaml      # Bot service account
│       ├── agent.yaml               # Telegram K8s agent
│       └── deployment.yaml          # Telegram bot Deployment
├── docs/                             # GitHub Pages site
│   ├── index.html                   # Landing page
│   └── kagent_fanout.gif            # Demo animation
├── .github/workflows/                # CI/CD
│   ├── telegram-bot-docker.yaml     # Build + push bot image to Docker Hub
│   └── f5-wrapper-docker.yaml       # Build + push F5 wrapper image to Docker Hub
├── blog-post-slack-agent.md          # Blog post: Building a Slack Bot with Kagent, MCP, and GitHub
└── terraform/                        # Infrastructure as Code
    └── f5-bigip/                    # F5 BIG-IP VIP configuration
        ├── main.tf                  # Provider config
        ├── variables.tf             # VIPs, NodePorts, node IPs
        ├── nodes.tf                 # K8s Talos nodes
        ├── monitors.tf              # Health monitors
        ├── pools.tf                 # LTM pools + attachments
        ├── virtual_servers.tf       # VIPs (172.16.20.60-80)
        └── outputs.tf               # VIP URLs
```

## Component Versions

| Component | Version | Chart Source |
|---|---|---|
| Argo CD | v2.14.11 | argoproj/argo-cd |
| HashiCorp Vault | 0.32.0 (app: 1.21.2) | helm.releases.hashicorp.com |
| External Secrets Operator | 2.0.1 | charts.external-secrets.io |
| Istio (ambient) | 1.29.0 | istio-release.storage.googleapis.com/charts |
| kagent | v0.8.0-beta6 | ghcr.io/kagent-dev/kagent/helm |
| Agentgateway | v1.1.0 | cr.agentgateway.dev/charts |
| OpenTelemetry Collector | 0.126.0 | ghcr.io/open-telemetry/opentelemetry-helm-charts |
| Gateway API CRDs | v1.4.0 | kubernetes-sigs/gateway-api |

## Sync Wave Order

Argo CD deploys components in this order to respect dependencies:

1. **Wave 0** - `vault` + `external-secrets` (secrets infrastructure)
2. **Wave 1** - `istio-base` (Istio CRDs)
3. **Wave 2** - `istiod` (control plane, requires CRDs)
4. **Wave 3** - `istio-cni` + `ztunnel` (data plane, requires istiod)
5. **Wave 4** - `kagent-crds` + `agentgateway-crds` (CRDs for Solo tools)
6. **Wave 5** - `kagent` + `agentgateway` (applications, require their CRDs)
7. **Wave 6** - `vault-config` + `kagent-examples` (SecretStore + ExternalSecrets + example agents)
8. **Wave 7** - `otel-collector` + `agentgateway-config` + `github-agent` + `slack-bot` + `telegram-bot` + `service-nodeports`

## Quick Start

### Prerequisites

- Talos Linux k8s cluster running
- `kubectl` configured to talk to the cluster
- Longhorn already installed (storage)
- `jq` installed locally (for vault-init script)

### Step 1: Bootstrap Argo CD

```bash
git clone https://github.com/ProfessorSeb/k8s-iceman.git
cd k8s-iceman
./bootstrap/install.sh
```

This installs Argo CD, Gateway API CRDs, and deploys the root App of Apps. Argo CD will begin deploying all components.

### Step 2: Initialize Vault

After Vault is deployed (check Argo CD UI), run:

```bash
./bootstrap/vault-init.sh
```

This will:
1. Initialize and unseal Vault
2. Enable the KV v2 secrets engine
3. Configure Kubernetes auth for External Secrets Operator
4. Prompt you for your LLM API key and store it in Vault

**Save the unseal key and root token** -- you'll need the unseal key any time Vault restarts.

### Step 3: Verify

```bash
# Check all Argo CD apps are synced
kubectl get applications -n argocd

# Check Vault is running
kubectl get pods -n vault

# Check the secret was created by ESO
kubectl get externalsecrets -n kagent
kubectl get secret kagent-llm-credentials -n kagent
```

### Access UIs

Services are exposed via F5 BIG-IP VIPs on the `172.16.20.x` network:

| Service | VIP URL | Credentials |
|---|---|---|
| Argo CD | `https://172.16.20.60` | user: `admin`, password: see below |
| Vault | `http://172.16.20.61:8200` | root token from vault-init |
| kagent | `http://172.16.20.62:8080` | n/a |

```bash
# Get Argo CD admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d && echo
```

**Fallback (port-forward):**
```bash
kubectl port-forward svc/argocd-server -n argocd 8443:443
kubectl port-forward -n vault svc/vault 8200:8200
kubectl port-forward -n kagent svc/kagent-ui 8080:8080
```

## Secrets Management

### How it works

```
Vault (source of truth) -> External Secrets Operator -> K8s Secret -> kagent
```

1. Secrets are stored in Vault at `secret/kagent/llm`
2. The `ClusterSecretStore` connects ESO to Vault via Kubernetes auth
3. The `ExternalSecret` pulls `api-key` from Vault and creates a K8s Secret
4. kagent's Helm chart references the K8s Secret for LLM credentials

### Add a new secret to Vault

```bash
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/<path> <key>=<value>
```

### Rotate a secret

1. Update the secret in Vault (same command as above)
2. ESO refreshes automatically (every 1h by default, configurable in `external-secret-kagent.yaml`)
3. Restart the consuming pod to pick up the new secret

## F5 BIG-IP Load Balancing

External access to cluster services is provided by an F5 BIG-IP (`172.16.10.10`) using VIPs on the `172.16.20.60-80` range. Managed via Terraform.

### VIP Assignments

| VIP | Port | Service | NodePort |
|---|---|---|---|
| `172.16.20.60` | 443 | Argo CD UI | 30443 |
| `172.16.20.61` | 8200 | Vault UI | 30820 |
| `172.16.20.62` | 8080 | kagent UI | 30808 |

### Terraform Setup

F5 BIG-IP credentials are stored in Vault at `secret/f5/bigip`.

```bash
cd terraform/f5-bigip

# Pull password from Vault
export TF_VAR_bigip_password=$(kubectl exec -n vault vault-0 -- \
  env VAULT_TOKEN=<token> vault kv get -field=password secret/f5/bigip)

terraform init
terraform plan
terraform apply
```

### Architecture

```
Client ──> F5 BIG-IP VIP (172.16.20.60:443)
               │ (automap SNAT)
               ├──> talos-cp:30443     (172.16.10.157)
               └──> talos-worker:30443 (172.16.10.160)
                        │
                    K8s NodePort Service
                        │
                    argocd-server pod
```

## Slack Bot (agentevals)

A Slack bot that connects your workspace to the **agentevals-agent** via the A2A protocol. The agent's job is to help manage the documentation site for agentevals (`agentevals-dev/website`).

### Features

- **HITL approvals** -- Block Kit Approve / Deny buttons for mutating GitHub operations
- **Thread context** -- multi-turn conversations via Slack threads
- **ask_user support** -- choice buttons or free-text replies for agent questions
- **Text-based approve/deny** -- type "approve" or "deny" in threads as an alternative to buttons

### Agent Tools

| MCP Server | Tools | Purpose |
|-----------|-------|---------|
| `slack-mcp` | `slack_post_message` | Post updates to Slack channels |
| `github-mcp` | `get_file_contents`, `create_or_update_file`, `push_files`, `create_branch`, `create_issue`, `create_pull_request`, etc. | Manage docs in agentevals-dev/website |

Mutating GitHub tools (`create_or_update_file`, `push_files`, `create_pull_request`, `merge_pull_request`) require HITL approval.

### Manifests

| File | Description |
|------|-------------|
| `manifests/slack-bot/01-external-secret.yaml` | Pulls Slack credentials from Vault |
| `manifests/slack-bot/02-slack-mcp.yaml` | Slack MCP server (stdio transport) |
| `manifests/slack-bot/03-agent.yaml` | agentevals-agent with Slack + GitHub tools |
| `manifests/slack-bot/04-deployment.yaml` | Slack bot Deployment (Socket Mode) |

### Deployment

**Image:** `docker.io/sebbycorp/kagent-slack-bot:latest`

```bash
# Store Slack credentials in Vault
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/slack \
    bot_token="xoxb-..." app_token="xapp-..." \
    team_id="T..." channel_ids="C..."

# ArgoCD auto-syncs from apps/slack-bot.yaml
# Or apply manually:
kubectl apply -f manifests/slack-bot/
```

### Local Development

```bash
cd apps/slack-bot-src
docker buildx build --platform linux/amd64,linux/arm64 \
  --tag docker.io/sebbycorp/kagent-slack-bot:latest --push .
kubectl rollout restart deployment slack-bot -n kagent
```

## Telegram Bot

A Telegram bot provides a chat interface to the kagent Kubernetes agent, allowing you to manage your cluster directly from Telegram.

### Features

- **Natural language K8s operations** -- ask the bot to create namespaces, apply manifests, inspect pods, check logs, scale deployments, and more
- **Human-in-the-loop (HITL) approval** -- mutating operations (`k8s_create_resource`, `k8s_apply_manifest`, `k8s_delete_resource`, `k8s_scale`) require explicit approval via inline **Approve** / **Reject** buttons before execution
- **Interactive questions (`ask_user`)** -- the agent can ask clarifying questions with selectable choices (inline buttons) or free-text input before taking action
- **Long-term memory** -- the agent remembers user preferences, namespace conventions, and past operations across conversations (vector-backed via `openai-embed`)
- **Context compaction** -- long conversations are automatically summarized so the agent doesn't lose track during extended debugging sessions
- **Session management** -- per-user conversation sessions with `/new` to reset
- **A2A protocol** -- communicates with kagent via the Agent-to-Agent (A2A) JSON-RPC protocol

### Agent Tools

The Telegram agent is focused on Kubernetes resource management:

| Category | Tools |
|----------|-------|
| **Read / Inspect** | `k8s_get_resources`, `k8s_describe_resource`, `k8s_get_pod_logs`, `k8s_get_events`, `k8s_get_resource_yaml`, `k8s_get_available_api_resources` |
| **Create / Mutate** (require approval) | `k8s_create_resource`, `k8s_apply_manifest`, `k8s_delete_resource`, `k8s_scale` |
| **Other Mutate** | `k8s_rollout`, `k8s_label_resource`, `k8s_annotate_resource` |

### How HITL Works in Telegram

When you ask the bot to perform a mutating operation (e.g., "create a staging namespace"), the flow is:

1. Bot sends your request to the kagent agent via A2A
2. Agent decides it needs to use a tool that requires approval (e.g., `k8s_create_resource`)
3. Agent returns an `input-required` status with the tool details wrapped in `adk_request_confirmation`
4. Bot parses the confirmation request and shows a clean summary:
   ```
   The agent wants to run: k8s_create_resource
   Tool 'k8s_create_resource' requires approval before execution.

   apiVersion: v1
   kind: Namespace
   metadata:
     name: staging

   [Approve] [Reject]
   ```
5. You tap a button, and the bot sends your decision back to the agent
6. Agent executes (or aborts) and returns the result
7. If the agent needs multiple approvals in sequence, each one is shown with new buttons

### How `ask_user` Works in Telegram

The agent can ask clarifying questions before taking action. The bot detects `ask_user` wrapped in `adk_request_confirmation` and renders it cleanly:

- **With choices** -- each choice is shown as a tappable inline button
- **Free-text** -- the question is displayed and the user's next message is captured as the answer

Example (free-text):
```
What namespace name should I create?

(Type your answer below)
```

Example (with choices):
```
Which environment do you want to deploy to?

[staging] [production] [development]
```

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help and available commands |
| `/new` | Reset your conversation session (also clears pending questions) |
| `/status` | Check connectivity to the kagent agent |

### Agent Configuration

The agent is configured with:
- **Memory** -- `memory.modelConfig: openai-embed` for persistent context across conversations (requires `openai-embed` ModelConfig from `00-shared-resources.yaml`)
- **Context compaction** -- `tokenThreshold: 120000`, `eventRetentionSize: 80`, `overlapSize: 8` for managing long conversations
- **System message** -- includes kagent built-in prompts for safety guardrails, tool usage best practices, and Kubernetes context

**Agent manifest:** `manifests/telegram-bot/agent.yaml`

### Deployment

The bot runs as a Deployment in the `kagent` namespace. It uses polling (no ingress needed).

**Image:** `docker.io/sebbycorp/telegram-kagent-bot:latest`

**Required secrets:**
- `TELEGRAM_BOT_TOKEN` -- from Vault via External Secrets (`secret/telegram`)
- `KAGENT_A2A_URL` -- set in the deployment manifest (points to kagent-controller A2A endpoint)

**Deployment manifest:** `manifests/telegram-bot/deployment.yaml`

### Prerequisites

The agent requires the `openai-embed` ModelConfig for memory. Ensure `00-shared-resources.yaml` is applied:

```bash
kubectl apply -f manifests/kagent-examples/00-shared-resources.yaml
```

### Local Development

```bash
cd apps/telegram-bot-src

# Build the image
docker build -t sebbycorp/telegram-kagent-bot:latest .

# Push to Docker Hub
docker push sebbycorp/telegram-kagent-bot:latest

# Restart the deployment to pick up the new image
kubectl rollout restart deployment telegram-bot -n kagent
```

## F5 BIG-IP Agent

An AI-powered agent for managing F5 BIG-IP load balancer infrastructure through natural language, running natively in Kubernetes via the kagent framework.

### Architecture

```
kagent UI / CLI / Telegram
        │
        ▼ (A2A)
kagent Engine (LLM)
        │
        ▼ (HTTP / OpenAPI auto-discovery)
F5 Wrapper Service (FastAPI pod)
        │
        ▼ (iControl REST / HTTPS)
F5 BIG-IP
```

The F5 Wrapper Service is a thin FastAPI app that proxies iControl REST calls, exposes a clean OpenAPI spec for kagent tool auto-discovery, and handles F5 token lifecycle in one place.

### Agent Capabilities

| Category | Operations |
|----------|-----------|
| **Pools** | List, create, delete pools; add/remove/enable/disable pool members |
| **Virtual Servers** | List, create, delete virtual servers with profiles, iRules, SNAT |
| **Nodes** | List, create, delete nodes; enable/disable across all pools |
| **Monitors** | List HTTP, HTTPS, TCP health monitors |
| **iRules** | List and inspect iRule definitions |
| **Certificates** | List SSL certificates and expiration dates |
| **System** | Version info, HA failover status, config sync, performance stats |

### Safety & Guardrails

- **Read-only mode** -- set `READ_ONLY=true` to block all write operations
- **Partition allow-list** -- `ALLOWED_PARTITIONS` restricts which F5 partitions the agent can access
- **Network policy** -- only kagent namespace can reach the wrapper; egress locked to the F5 management IP
- **HA awareness** -- the agent checks failover status before write operations
- **System prompt** -- instructs the agent to confirm destructive operations and check dependencies

### Manifests

| File | Description |
|------|-------------|
| `manifests/kagent-examples/f5-agent/bigip/01-external-secret.yaml` | Pulls F5 credentials from Vault |
| `manifests/kagent-examples/f5-agent/bigip/02-agent.yaml` | kagent Agent CRD with system prompt and tool config |
| `manifests/kagent-examples/f5-agent/bigip/03-deployment.yaml` | Wrapper Deployment, Service (with OpenAPI annotation), NetworkPolicy |

### Deployment

```bash
# Ensure F5 credentials are stored in Vault
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/f5 host="https://10.1.1.245" username="admin" password="<password>"

# Apply the manifests
kubectl apply -f manifests/kagent-examples/f5-agent/bigip/

# Verify the wrapper is running
kubectl get pods -n kagent -l app=f5-wrapper
kubectl logs -n kagent -l app=f5-wrapper
```

### Local Development

```bash
cd apps/f5-wrapper

# Build the image
docker build -t sebbycorp/f5-wrapper:latest .

# Push to Docker Hub
docker push sebbycorp/f5-wrapper:latest

# Restart the deployment
kubectl rollout restart deployment f5-wrapper -n kagent
```

## FortiGate Agent

An AI-powered agent for managing FortiGate firewalls through natural language, running natively in Kubernetes via the kagent framework. Uses a community FortiGate MCP server for tool discovery.

### Agent Capabilities

| Category | Operations |
|----------|-----------|
| **Firewall Policies** | List, get, enable, disable policies; create temporary block policies |
| **NAT** | List central SNAT entries, IP pools, Virtual IPs (destination NAT) |
| **Network Objects** | List address objects, address groups, services, service groups |
| **Interfaces & Routing** | List network interfaces and static routes |
| **System** | Status (hostname, firmware, serial, uptime), resource utilization, HA status |
| **DHCP & Discovery** | List DHCP leases, detected/discovered devices, device fingerprinting |
| **Wireless / FortiAP** | List wireless clients, SSIDs; disconnect clients; enable/disable SSIDs |

### Safety & Guardrails

- Confirms destructive operations before executing (disable policy, block device, disable SSID)
- Prefers targeted actions (disconnect one client) over broad ones (disable entire SSID)
- Starts with read-only discovery tools before taking control actions
- Network policy restricts access to FortiGate management network only

### Manifests

| File | Description |
|------|-------------|
| `manifests/kagent-examples/fortigate-agent/01-external-secret.yaml` | Pulls FortiGate credentials from Vault |
| `manifests/kagent-examples/fortigate-agent/02-agent.yaml` | kagent Agent CRD with FortiGate system prompt |
| `manifests/kagent-examples/fortigate-agent/03-deployment.yaml` | FortiGate MCP server Deployment, Service, RemoteMCPServer, NetworkPolicy |

### Deployment

```bash
# Store FortiGate credentials in Vault
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/fortigate FORTI_HOST="https://172.16.x.x" FORTI_TOKEN="<api-token>"

# Apply the manifests
kubectl apply -f manifests/kagent-examples/fortigate-agent/

# Verify the MCP server is running
kubectl get pods -n kagent -l app=fortigate-mcp-server
```

## Moat Sandbox Coder Agent

A sandboxed code execution agent that uses **Moat** to create isolated Linux environments. The agent can write, run, and test code in ephemeral sandboxes with snapshot/restore support.

### How It Works

The agent connects to an external Moat sandbox pool manager via a `RemoteMCPServer`. Each sandbox is a fully isolated Linux environment with Python 3, pip, git, and standard tools. Sandboxes have their own filesystem, CPU, and memory limits.

### Agent Tools

| Tool | Purpose |
|------|---------|
| `create_sandbox` / `delete_sandbox` | Manage sandbox lifecycle |
| `shell` / `run_code` | Execute commands or code in sandboxes |
| `write_file` / `read_file` / `list_files` | File operations inside sandboxes |
| `take_snapshot` / `restore_snapshot` / `list_snapshots` | Checkpoint and rollback sandbox state |
| `get_pool_status` / `list_sandboxes` | Pool and sandbox management |
| `list_sessions` / `delete_session` | Session management for multi-conversation work |

### Manifests

| File | Description |
|------|-------------|
| `manifests/kagent-examples/moat-agent/01-moat-mcp.yaml` | RemoteMCPServer pointing to Moat pool manager |
| `manifests/kagent-examples/moat-agent/02-moat-sandbox-coder.yaml` | Agent CRD with sandbox coding system prompt |

### Deployment

```bash
# Requires an external Moat sandbox pool manager running and accessible
# The MCP server URL is configured in 01-moat-mcp.yaml

kubectl apply -f manifests/kagent-examples/moat-agent/
```

## GitHub Agent

An agent for managing the agentevals documentation site (`agentevals-dev/website`). Creates issues, pull requests, and edits documentation via a GitHub MCP server.

### Manifests

| File | Description |
|------|-------------|
| `manifests/github-agent/01-external-secret.yaml` | Pulls GitHub token from Vault |
| `manifests/github-agent/02-github-mcp.yaml` | GitHub MCP server |
| `manifests/github-agent/03-agent.yaml` | GitHub agent with docs management tools |

## kagent Examples

The `manifests/kagent-examples/` directory contains reusable agent patterns and examples:

| Directory | Description |
|-----------|-------------|
| `00-shared-resources.yaml` | Shared ModelConfigs (e.g., `openai-embed` for memory) |
| `context-management/` | Context compaction configuration examples |
| `f5-agent/bigip/` | F5 BIG-IP agent with wrapper deployment |
| `fortigate-agent/` | FortiGate firewall agent with MCP server |
| `git-skills/` | Loading agent skills from public/private git repos |
| `human-in-the-loop/` | HITL approval patterns (tool approval, ask_user) |
| `memory/` | Agent memory with vector-backed recall |
| `moat-agent/` | Moat sandbox coder agent with remote MCP |
| `multi-runtime/` | Go and Python agent runtime examples |
| `prompt-templates/` | Templated agent system prompts |
| `telegram-f5-bot/` | Telegram bot wired to the F5 agent |
| `telegram-forti-bot/` | Telegram bot wired to the FortiGate agent |
| `tools/` | Tool configuration patterns (full K8s, multi-agent, Istio) |
| `vault-agent/` | Vault management agent |

## CI/CD

### Telegram Bot Docker Image

A GitHub Actions workflow automatically builds and pushes the Telegram bot Docker image when changes are made to `apps/telegram-bot-src/`.

**Workflow:** `.github/workflows/telegram-bot-docker.yaml`

**Triggers:**
- Push to `main` when `apps/telegram-bot-src/` files change
- Manual trigger via `workflow_dispatch`
- PRs build the image (for validation) but do not push

**Image tags:**
- `latest` -- on pushes to `main`
- `<git-sha>` -- on every build (e.g., `sebbycorp/telegram-kagent-bot:a1b2c3d`)

**Required GitHub secrets:**
- `DOCKERHUB_USERNAME` -- Docker Hub username
- `DOCKERHUB_TOKEN` -- Docker Hub access token

### F5 Wrapper Docker Image

A GitHub Actions workflow automatically builds and pushes the F5 wrapper Docker image when changes are made to `apps/f5-wrapper/`.

**Workflow:** `.github/workflows/f5-wrapper-docker.yaml`

**Triggers:**
- Push to `main` when `apps/f5-wrapper/` files change
- Manual trigger via `workflow_dispatch`
- PRs build the image (for validation) but do not push

**Image tags:**
- `latest` -- on pushes to `main`
- `<git-sha>` -- on every build (e.g., `sebbycorp/f5-wrapper:a1b2c3d`)

## Making Changes (GitOps Workflow)

**All changes flow through git. Never `helm install` or `kubectl apply` directly.**

| Action | What to edit | Then |
|---|---|---|
| Update Helm values | `helm-values/<component>/values.yaml` | Push to `main` |
| Upgrade a version | `targetRevision` in `apps/<component>.yaml` | Push to `main` |
| Add a component | New YAML in `apps/` + `helm-values/` | Push to `main` |
| Remove a component | Delete YAML from `apps/` | Push to `main` |
| Add a secret | Store in Vault, add ExternalSecret in `manifests/vault-config/` | Push to `main` |
