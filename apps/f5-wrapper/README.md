# F5 BIG-IP Wrapper API

A lightweight FastAPI service that wraps the F5 BIG-IP iControl REST API and exposes it as both a REST API and an MCP (Model Context Protocol) tool server. Built specifically for consumption by [kagent](https://kagent.dev) — a Kubernetes-native AI agent framework — so an LLM-powered agent can manage F5 load balancer infrastructure through natural language.

## Why a Wrapper?

The F5 iControl REST API is massive, uses token-based auth with expiring sessions, and returns deeply nested JSON. Talking to it directly from an AI agent would be fragile and wasteful. This wrapper solves that by:

1. **Exposing only what the agent needs** — a curated set of pool, virtual server, node, monitor, iRule, certificate, and system operations instead of the full iControl surface area
2. **Handling auth in one place** — token acquisition, automatic refresh (before the 1200s expiry), and clean logout on shutdown
3. **Speaking MCP natively** — kagent discovers all 28 tools via the `/mcp` endpoint using streamable HTTP, no manual tool registration needed
4. **Adding guardrails** — read-only mode toggle, partition allow-lists, HITL approval for destructive operations, and clear error responses the agent can reason about

## Architecture

```
User (natural language via kagent UI, Telegram, or A2A)
    │
    ▼
kagent Controller (routes to f5-bigip-agent)
    │
    ▼
LLM decides which MCP tool to call (e.g., "list_pools", "set_node_state")
    │
    ▼
kagent invokes tool via MCP streamable-HTTP → f5-wrapper /mcp endpoint
    │
    ▼
F5 Wrapper translates to iControl REST → F5 BIG-IP
    │
    ▼
Response flows back → LLM formats a human-readable answer
```

### kagent Integration

The wrapper integrates with kagent through two Kubernetes resources:

**RemoteMCPServer** — tells kagent where to discover tools:
```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: f5-wrapper-mcp
spec:
  url: "http://f5-wrapper.kagent:8080/mcp"
```

**Agent CRD** — binds the MCP server to an agent with HITL approval for destructive ops:
```yaml
tools:
  - type: McpServer
    mcpServer:
      kind: RemoteMCPServer
      name: f5-wrapper-mcp
      requireApproval:
        - create_pool
        - delete_pool
        - delete_virtual_server
        # ... all write operations
```

## MCP Tools (28 total)

The MCP server at `/mcp` exposes these tools for kagent:

### Pools (8 tools)
| Tool | Description |
|------|-------------|
| `list_pools` | List all LTM pools in a partition |
| `get_pool` | Get pool details with members and status |
| `create_pool` | Create a new pool with optional members |
| `delete_pool` | Delete a pool |
| `list_pool_members` | List all members of a pool |
| `add_pool_member` | Add a member (address:port) to a pool |
| `remove_pool_member` | Remove a member from a pool |
| `set_pool_member_state` | Enable/disable/force-offline a member |

### Virtual Servers (4 tools)
| Tool | Description |
|------|-------------|
| `list_virtual_servers` | List all virtual servers |
| `get_virtual_server` | Get VS details (profiles, iRules, pool) |
| `create_virtual_server` | Create a virtual server |
| `delete_virtual_server` | Delete a virtual server |

### Nodes (5 tools)
| Tool | Description |
|------|-------------|
| `list_nodes` | List all nodes |
| `get_node` | Get node details |
| `create_node` | Create a node |
| `delete_node` | Delete a node |
| `set_node_state` | Enable/disable/force-offline a node |

### Monitors (4 tools)
| Tool | Description |
|------|-------------|
| `list_monitors` | List all monitors |
| `list_http_monitors` | List HTTP monitors |
| `list_https_monitors` | List HTTPS monitors |
| `list_tcp_monitors` | List TCP monitors |

### iRules (2 tools)
| Tool | Description |
|------|-------------|
| `list_irules` | List all iRules |
| `get_irule` | Get iRule definition (TCL code) |

### Certificates (2 tools)
| Tool | Description |
|------|-------------|
| `list_certificates` | List all SSL certificates |
| `get_certificate` | Get certificate details |

### System (4 tools)
| Tool | Description |
|------|-------------|
| `system_info` | BIG-IP version, hostname, platform |
| `failover_status` | HA failover status (active/standby) |
| `system_performance` | Throughput, connections, CPU, memory |
| `config_sync_status` | Config sync status across HA peers |

### REST API

The same operations are also available as REST endpoints under `/api/v1/` for direct HTTP access. The OpenAPI docs are at `/docs`.

### Health Check

`GET /health` returns `{"status": "ok"}` — used by Kubernetes liveness and readiness probes.

## Configuration

All configuration is via environment variables (loaded by Pydantic Settings):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `F5_HOST` | Yes | — | F5 management URL, e.g. `https://10.1.1.245` |
| `F5_USERNAME` | No | `admin` | iControl REST username |
| `F5_PASSWORD` | Yes | — | iControl REST password |
| `F5_VERIFY_SSL` | No | `false` | Verify F5 SSL certificate |
| `F5_PARTITION` | No | `Common` | Default BIG-IP partition |
| `ALLOWED_PARTITIONS` | No | `Common` | Comma-separated partition allow-list |
| `READ_ONLY` | No | `false` | Block all write operations (create, delete, state changes) |

In Kubernetes, `F5_HOST`, `F5_USERNAME`, and `F5_PASSWORD` are injected from a Secret (`f5-credentials`) that is populated by External Secrets Operator from Vault.

## Project Structure

```
f5-wrapper/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py              # FastAPI app + MCP mount at /mcp
│   ├── mcp_server.py         # MCP tool definitions (28 tools)
│   ├── config.py             # Pydantic Settings — env var configuration
│   ├── auth.py               # F5 token manager (login, refresh, logout)
│   ├── routers/
│   │   ├── pools.py          # Pool + pool member CRUD (REST)
│   │   ├── virtual_servers.py # Virtual server CRUD (REST)
│   │   ├── nodes.py          # Node CRUD + state management (REST)
│   │   ├── monitors.py       # Health monitor listing (REST)
│   │   ├── irules.py         # iRule listing (REST)
│   │   ├── certificates.py   # SSL certificate listing (REST)
│   │   └── system.py         # System info, failover, performance (REST)
│   └── utils/
│       └── f5_client.py      # Reusable HTTP client for iControl REST
```

## How the Auth Works

The F5 iControl REST API uses token-based authentication. The wrapper handles this transparently:

1. **On startup** — `F5TokenManager.login()` calls `/mgmt/shared/authn/login` and stores the token
2. **On each request** — `F5Client` calls `get_headers()` which checks if the token is expired (tokens last 1200s, we refresh at 960s) and re-authenticates if needed
3. **On shutdown** — `F5TokenManager.logout()` deletes the token via `/mgmt/shared/authz/tokens/{token}`

The token is shared across both REST routers and MCP tools via `app.state` and a module-level reference in `mcp_server.py`.

## Local Development

```bash
# Set required env vars
export F5_HOST="https://10.1.1.245"
export F5_USERNAME="admin"
export F5_PASSWORD="your-password"

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# View the auto-generated docs
# http://localhost:8080/docs         (Swagger UI for REST API)
# http://localhost:8080/openapi.json (raw OpenAPI spec)
# MCP endpoint: http://localhost:8080/mcp
```

## Docker

```bash
# Build
docker build -t sebbycorp/f5-wrapper:latest .

# Run
docker run -p 8080:8080 \
  -e F5_HOST="https://10.1.1.245" \
  -e F5_USERNAME="admin" \
  -e F5_PASSWORD="your-password" \
  sebbycorp/f5-wrapper:latest

# Push
docker push sebbycorp/f5-wrapper:latest
```

## Kubernetes Deployment

The wrapper runs as a Deployment in the `kagent` namespace alongside the kagent controller. See the manifests in `manifests/kagent-examples/f5-agent/`:

| Manifest | What it does |
|----------|-------------|
| `01-external-secret.yaml` | Pulls F5 credentials from Vault into a K8s Secret |
| `02-agent.yaml` | kagent Agent CRD — system prompt, skills, HITL approval, MCP tool binding |
| `03-deployment.yaml` | Deployment, Service, RemoteMCPServer CRD, NetworkPolicy |

### Setup

```bash
# 1. Store F5 creds in Vault
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/f5 \
    host="https://10.1.1.245" \
    username="admin" \
    password="<password>"

# 2. Deploy (or let ArgoCD sync automatically)
kubectl apply -f manifests/kagent-examples/f5-agent/

# 3. Verify
kubectl get pods -n kagent -l app=f5-wrapper
kubectl get agents f5-bigip-agent -n kagent
kubectl get remotemcpservers f5-wrapper-mcp -n kagent

# 4. Test the MCP endpoint
kubectl run curl --rm -it --image=curlimages/curl -- \
  curl -s http://f5-wrapper.kagent:8080/health
```

## Safety Guardrails

| Guardrail | How it works |
|-----------|-------------|
| **HITL approval** | 10 destructive MCP tools require human approval via kagent's `requireApproval` before execution |
| **Read-only mode** | Set `READ_ONLY=true` — all write operations return an error |
| **Partition allow-list** | `ALLOWED_PARTITIONS` restricts which F5 partitions can be accessed |
| **Network isolation** | NetworkPolicy allows ingress only from kagent namespace, egress only to F5 mgmt IP + DNS |
| **Agent-level confirmation** | System prompt instructs the LLM to summarize impact and confirm before destructive ops |
| **HA awareness** | System prompt tells the agent to check failover status before writes |

## CI/CD

A GitHub Actions workflow (`.github/workflows/f5-wrapper-docker.yaml`) automatically builds and pushes the Docker image when files in `apps/f5-wrapper/` change on `main`. PRs build the image for validation but don't push.
