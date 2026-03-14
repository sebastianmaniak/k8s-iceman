# F5 BIG-IP Wrapper API

A lightweight FastAPI service that wraps the F5 BIG-IP iControl REST API and exposes it as a clean, OpenAPI-documented HTTP API. Built specifically for consumption by [kagent](https://kagent.dev) — a Kubernetes-native AI agent framework — so an LLM-powered agent can manage F5 load balancer infrastructure through natural language.

## Why a Wrapper?

The F5 iControl REST API is massive, uses token-based auth with expiring sessions, and returns deeply nested JSON. Talking to it directly from an AI agent would be fragile and wasteful. This wrapper solves that by:

1. **Exposing only what the agent needs** — a curated set of pool, virtual server, node, monitor, iRule, certificate, and system operations instead of the full iControl surface area
2. **Handling auth in one place** — token acquisition, automatic refresh (before the 1200s expiry), and clean logout on shutdown
3. **Producing clean OpenAPI schemas** — FastAPI auto-generates `/openapi.json`, which kagent uses to auto-discover all available tools without any manual registration
4. **Adding guardrails** — read-only mode toggle, partition allow-lists, and clear HTTP error responses the agent can reason about

## How It Integrates with kagent

```
User (natural language)
    │
    ▼
kagent Engine (sends prompt + tool definitions to LLM)
    │
    ▼
LLM decides which tool to call (e.g., "list_pools" or "set_member_state")
    │
    ▼
kagent invokes this wrapper via HTTP
    │
    ▼
Wrapper translates to iControl REST → F5 BIG-IP
    │
    ▼
Response flows back → LLM formats a human-readable answer
```

The key integration point is the Kubernetes Service annotation:

```yaml
annotations:
  kagent.dev/openapi-path: "/openapi.json"
```

This tells kagent to fetch the OpenAPI spec from the wrapper and register every endpoint as an available tool for the agent. No manual tool definitions needed — add a new router and kagent picks it up automatically.

## API Endpoints

### Pools (`/api/v1/pools`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all pools in a partition |
| `GET` | `/{pool_name}` | Get pool details with members and status |
| `POST` | `/` | Create a new pool with optional members |
| `DELETE` | `/{pool_name}` | Delete a pool |
| `GET` | `/{pool_name}/members` | List all members of a pool |
| `POST` | `/{pool_name}/members` | Add a member to a pool |
| `DELETE` | `/{pool_name}/members/{member_name}` | Remove a member from a pool |
| `PATCH` | `/{pool_name}/members/{member_name}/state` | Enable/disable/force-offline a member |

### Virtual Servers (`/api/v1/virtual-servers`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all virtual servers |
| `GET` | `/{vs_name}` | Get virtual server details (profiles, iRules, pool) |
| `POST` | `/` | Create a virtual server |
| `DELETE` | `/{vs_name}` | Delete a virtual server |

### Nodes (`/api/v1/nodes`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all nodes |
| `GET` | `/{node_name}` | Get node details |
| `POST` | `/` | Create a node |
| `DELETE` | `/{node_name}` | Delete a node |
| `PATCH` | `/{node_name}/state` | Enable/disable/force-offline a node |

### Monitors (`/api/v1/monitors`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all monitors |
| `GET` | `/http` | List HTTP monitors |
| `GET` | `/https` | List HTTPS monitors |
| `GET` | `/tcp` | List TCP monitors |

### iRules (`/api/v1/irules`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all iRules |
| `GET` | `/{irule_name}` | Get iRule definition (TCL code) |

### Certificates (`/api/v1/certificates`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all SSL certificates |
| `GET` | `/{cert_name}` | Get certificate details |

### System (`/api/v1/system`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/info` | BIG-IP version, hostname, platform |
| `GET` | `/failover-status` | HA failover status (active/standby) |
| `GET` | `/performance` | Throughput, connections, CPU, memory |
| `GET` | `/config-sync-status` | Config sync status across HA peers |

### Health (`/health`)

Returns `{"status": "ok"}` — used by Kubernetes liveness and readiness probes.

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
│   ├── main.py              # FastAPI app with lifespan (token init/teardown)
│   ├── config.py             # Pydantic Settings — env var configuration
│   ├── auth.py               # F5 token manager (login, refresh, logout)
│   ├── routers/
│   │   ├── pools.py          # Pool + pool member CRUD
│   │   ├── virtual_servers.py # Virtual server CRUD
│   │   ├── nodes.py          # Node CRUD + state management
│   │   ├── monitors.py       # Health monitor listing
│   │   ├── irules.py         # iRule listing
│   │   ├── certificates.py   # SSL certificate listing
│   │   └── system.py         # System info, failover, performance
│   ├── models/               # (Pydantic models are co-located in routers)
│   └── utils/
│       └── f5_client.py      # Reusable HTTP client for iControl REST
└── tests/
```

## How the Auth Works

The F5 iControl REST API uses token-based authentication. The wrapper handles this transparently:

1. **On startup** — `F5TokenManager.login()` calls `/mgmt/shared/authn/login` and stores the token
2. **On each request** — `F5Client` calls `get_headers()` which checks if the token is expired (tokens last 1200s, we refresh at 960s) and re-authenticates if needed
3. **On shutdown** — `F5TokenManager.logout()` deletes the token via `/mgmt/shared/authz/tokens/{token}`

The token is stored in `app.state` so it's shared across all requests without creating a new session per call.

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

# View the auto-generated OpenAPI docs
# http://localhost:8080/docs       (Swagger UI)
# http://localhost:8080/openapi.json (raw spec)
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
| `02-agent.yaml` | kagent Agent CRD — system prompt, memory, tool binding |
| `03-deployment.yaml` | Deployment, Service (with OpenAPI annotation), NetworkPolicy |

```bash
# Store F5 creds in Vault first
kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> \
  vault kv put secret/f5 host="https://10.1.1.245" username="admin" password="<password>"

# Deploy everything
kubectl apply -f manifests/kagent-examples/f5-agent/

# Verify
kubectl get pods -n kagent -l app=f5-wrapper
curl http://f5-wrapper.kagent.svc:8080/health
curl http://f5-wrapper.kagent.svc:8080/openapi.json | jq '.paths | keys'
```

## Safety Guardrails

| Guardrail | How it works |
|-----------|-------------|
| **Read-only mode** | Set `READ_ONLY=true` — all POST/PATCH/DELETE endpoints return 403 |
| **Partition allow-list** | `ALLOWED_PARTITIONS` restricts which F5 partitions can be accessed |
| **Network isolation** | NetworkPolicy allows ingress only from kagent namespace, egress only to F5 mgmt IP + DNS |
| **Agent-level confirmation** | The kagent Agent CRD system prompt instructs the LLM to confirm before destructive ops |
| **HA awareness** | System prompt tells the agent to check failover status before writes |

## CI/CD

A GitHub Actions workflow (`.github/workflows/f5-wrapper-docker.yaml`) automatically builds and pushes the Docker image when files in `apps/f5-wrapper/` change on `main`. PRs build the image for validation but don't push.
