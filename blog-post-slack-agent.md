---
title: "Building a Slack Bot That Updates Your Docs: Kagent, MCP, and GitHub in Action"
publishDate: 2026-03-20
description: "How to build a Kubernetes-native Slack bot that manages documentation through natural language using Kagent, GitHub MCP Server, and human-in-the-loop approvals."
---

## The Problem Nobody Talks About

Documentation rots. Everyone knows it, nobody wants to fix it. The friction is real — switching context from a Slack conversation about a bug to opening a browser, navigating to a repo, editing markdown, committing, pushing, and opening a PR. By the time you've done all that, the conversation has moved on and the motivation is gone.

What if updating docs was as simple as telling a bot in Slack, "Hey, update the getting started guide to mention the new evaluator type"?

That's exactly what this project does. A Slack bot, running inside Kubernetes, powered by an AI agent framework called Kagent, connected to GitHub through the Model Context Protocol (MCP). You talk to it in Slack. It reads the docs, makes changes, opens PRs — and asks for your approval before doing anything destructive.

No more context switching. No more stale docs.

---

## Why This Exists

Three pain points drove this build:

**1. Teams live in Slack.** That's where decisions happen, where context lives, where people already are. Forcing someone out of Slack to manage docs is a guaranteed way to ensure docs never get updated.

**2. LLMs are good at writing, bad at acting.** ChatGPT can draft a paragraph, sure. But it can't read your existing docs, understand the structure, create a branch, commit the change, and open a PR. You need tools — real, connected tools — not just text generation.

**3. Trust requires guardrails.** Nobody wants an AI silently pushing code to main. The agent needs to ask permission before it mutates anything. Human-in-the-loop isn't a nice-to-have, it's a requirement.

---

## The Architecture

Here's the high-level flow:

```
Slack message
    ↓
Slack Bot (Socket Mode, no ingress required)
    ↓
Kagent Controller (A2A protocol)
    ↓
Agent with MCP tools
    ├── slack-mcp → Post status updates back to Slack
    └── github-mcp → Read files, create branches, commit, open PRs
    ↓
GitHub (via Copilot hosted MCP endpoint)
```

The entire stack runs on Kubernetes. No external servers, no Lambda functions, no third-party SaaS platforms mediating the conversation. Everything is declared as Kubernetes resources, managed through GitOps, and secured with proper secrets management.

---

## What is Kagent?

Kagent is a Kubernetes-native AI agent framework. Think of it as a controller that manages AI agents the same way Kubernetes manages pods — through declarative YAML manifests.

Instead of writing a Python script that calls OpenAI and bolts on some tools, you declare an `Agent` custom resource:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: docs-agent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    tools:
      - type: McpServer
        mcpServer:
          kind: RemoteMCPServer
          name: github-mcp
      - type: McpServer
        mcpServer:
          kind: MCPServer
          name: slack-mcp
```

That's it. Kagent handles:

- **LLM routing** — Which model to call, with what credentials
- **Tool orchestration** — MCP servers are mounted as tools the agent can invoke
- **Memory** — Embedding-based long-term context so the agent remembers past conversations
- **Context compaction** — When conversations get long, Kagent compresses earlier messages to stay within token limits
- **A2A protocol** — Agent-to-Agent communication, which is how the Slack bot talks to the Kagent controller

The agent isn't a monolith. It's a composition of capabilities declared in YAML and reconciled by a Kubernetes controller. Add a new tool? Add a line to the manifest. Change the model? Update a config reference. Everything follows the Kubernetes pattern — desired state in git, actual state in the cluster, a controller reconciling the difference.

---

## How GitHub MCP Server Fits In

The Model Context Protocol (MCP) is a standard for giving AI agents access to tools. Instead of writing custom integrations for every service, you expose tools through an MCP server, and any MCP-compatible agent can use them.

For GitHub, this means the agent gets access to operations like:

- `get_file_contents` — Read any file in a repository
- `create_or_update_file` — Edit files and commit changes
- `create_branch` — Work on feature branches, not main
- `create_pull_request` — Open PRs for review
- `search_code` — Find relevant files across the repo
- `list_issues`, `create_issue` — Manage issues alongside docs

The GitHub MCP server is declared as a `RemoteMCPServer` resource in Kubernetes:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer
metadata:
  name: github-mcp
spec:
  transport:
    type: STREAMABLE_HTTP
    streamableHTTP:
      url: https://api.githubcopilot.com/mcp/
      timeout: 5s
      headersFrom:
        - name: Authorization
          valueFrom:
            type: Secret
            name: github-pat
```

A few things worth noting:

- **No self-hosted binary.** This uses GitHub Copilot's hosted MCP endpoint. No containers to build, no sidecars to manage.
- **Auth through Kubernetes secrets.** The GitHub PAT is stored in HashiCorp Vault, synced to a Kubernetes secret via External Secrets Operator, and injected as a Bearer token header. The agent never sees the raw token.
- **Streamable HTTP transport.** The MCP server uses HTTP with streaming support, so long-running operations don't time out.

---

## The Slack Bot: Bridging Chat and Code

The Slack bot is the user-facing piece. It runs in Socket Mode — meaning it opens a websocket connection to Slack's API rather than requiring an inbound webhook URL. This is a deliberate choice: no ingress controller needed, no public endpoints exposed, no certificates to manage.

When a message comes in, the bot forwards it to the Kagent controller using the A2A (Agent-to-Agent) protocol over HTTP:

```python
async with httpx.AsyncClient() as client:
    response = await client.post(
        f"{KAGENT_BASE_URL}/api/a2a/kagent/{AGENT_NAME}/",
        json=payload
    )
```

The agent processes the request — which might involve reading docs from GitHub, drafting changes, and preparing a PR — then returns a response. But here's the critical part: **mutating operations require human approval.**

When the agent wants to run `create_or_update_file`, `push_files`, `create_pull_request`, or `merge_pull_request`, the Slack bot renders an approval prompt:

> **Tool: create_pull_request**
> Repository: my-org/website
> Title: "Update getting started guide with new evaluator type"
> Branch: docs/update-evaluators
>
> [ Approve ] [ Deny ]

The user clicks a button in Slack. Only then does the agent execute the action. This isn't just a safety measure — it's a trust-building mechanism. People adopt tools they can control.

---

## Secrets Management: The Boring Part That Matters

Every credential in this system flows through a single pipeline:

```
HashiCorp Vault → External Secrets Operator → Kubernetes Secret → Application
```

Vault stores the truth:
- LLM API keys at `secret/kagent/llm`
- Slack bot tokens at `secret/slack`
- GitHub PAT at `secret/github`

External Secrets Operator polls Vault every hour and syncs secrets into Kubernetes. Applications reference Kubernetes secrets in their manifests. Nobody hardcodes a token. Nobody copy-pastes a key into a YAML file.

The bootstrap script initializes everything:

```bash
# Initialize Vault, configure K8s auth, create policies
./bootstrap/vault-init.sh
```

It prompts for each secret interactively, stores them in Vault, and configures the Kubernetes auth method so ESO can retrieve them. One script, one time, all secrets in the right place.

---

## Deployment: GitOps All the Way Down

The entire stack deploys through Argo CD using an App of Apps pattern with sync waves:

| Wave | What Deploys | Why This Order |
|------|-------------|----------------|
| 0 | Vault, External Secrets Operator | Secrets infrastructure first |
| 1-3 | Istio (ambient mesh) | Zero-trust networking |
| 4 | Kagent CRDs | Agent framework schema |
| 5 | Kagent controller | Agent runtime |
| 6 | Vault config (SecretStores, ExternalSecrets) | Wire secrets to apps |
| 7 | Slack bot, GitHub agent | Application layer |

One `git push` to main and Argo CD reconciles everything. Auto-prune removes deleted resources. Auto-heal fixes drift. If someone `kubectl delete`s a resource, Argo puts it back.

The bootstrap is two commands:

```bash
./bootstrap/install.sh    # Argo CD + Gateway API CRDs
./bootstrap/vault-init.sh  # Secrets initialization
```

After that, git is the only interface.

---

## What a Real Interaction Looks Like

Here's a realistic conversation in Slack:

> **You:** The docs for the scoring evaluator are missing the threshold parameter. Can you add it to the reference page?
>
> **Agent:** I'll check the current docs. Let me read the evaluator reference page.
>
> *(Agent calls `get_file_contents` on the docs repo)*
>
> **Agent:** Found it. The scoring evaluator section at `content/docs/evaluators/scoring.md` doesn't mention the `threshold` parameter. I'll create a branch and update the docs.
>
> *(Agent calls `create_branch` → `create_or_update_file`)*
>
> **⚠️ Approval Required**
> **Tool:** create_or_update_file
> **File:** content/docs/evaluators/scoring.md
> **Branch:** docs/add-threshold-param
>
> [ Approve ] [ Deny ]
>
> **You:** *clicks Approve*
>
> **Agent:** Done. I've also opened a PR for review.
>
> **⚠️ Approval Required**
> **Tool:** create_pull_request
> **Title:** "Add threshold parameter to scoring evaluator docs"
>
> [ Approve ] [ Deny ]
>
> **You:** *clicks Approve*
>
> **Agent:** PR #42 is open. Here's the link: [PR #42]

Total time: under a minute. No browser tabs opened. No git commands typed. No context lost.

---

## Lessons Learned

**Socket Mode is underrated.** Not needing ingress for a Slack bot simplifies everything. No TLS certs, no DNS records, no service exposure. The bot dials out; Slack sends messages over the websocket. For internal tools, this is the way.

**MCP is the right abstraction for agent tools.** Before MCP, every AI agent had bespoke tool integrations — custom functions wrapping API calls. MCP standardizes this. Swap GitHub for GitLab? Replace one MCP server, the agent doesn't change. The protocol decouples the agent from the tools.

**Human-in-the-loop needs good UX.** A wall of JSON asking "approve this?" doesn't cut it. The approval prompt needs to clearly show what action is being taken, on what resource, with what parameters. Slack's interactive blocks make this possible — buttons, structured messages, and threaded conversations.

**GitOps + AI agents work well together.** The agent configurations are YAML in git. The secrets pipeline is declarative. The deployment order is encoded in sync waves. When something breaks, `git log` tells you what changed. When you want to add a new agent, you add manifests and push. The operational model is the same as any other Kubernetes workload.

**Start with read-only, add writes carefully.** The first version of this agent could only read docs and post to Slack. Writes came later, gated behind approvals. This incremental approach builds confidence — both in the system and in the team using it.

---

## Try It Yourself

The building blocks are all open source:

- **[Kagent](https://github.com/kagent-dev/kagent)** — Kubernetes-native AI agent framework
- **[GitHub MCP Server](https://api.githubcopilot.com/mcp/)** — GitHub's hosted MCP endpoint (requires a GitHub PAT)
- **[Slack Bolt](https://slack.dev/bolt-python/)** — Python framework for Slack apps with Socket Mode support
- **[External Secrets Operator](https://external-secrets.io/)** — Sync secrets from Vault (or other providers) to Kubernetes

The pattern generalizes beyond documentation. Any workflow that involves reading from a system, deciding on an action, getting human approval, and executing — that's an agent use case. Incident response, infrastructure changes, onboarding checklists. The Slack bot is the interface. Kagent is the brain. MCP servers are the hands.

The gap between "we should update the docs" and "the docs are updated" just got a lot smaller.
