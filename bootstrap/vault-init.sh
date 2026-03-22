#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Vault Post-Deploy Initialization
# Run this ONCE after Vault is deployed by Argo CD
# =============================================================================

VAULT_NAMESPACE="vault"
VAULT_POD="vault-0"
ESO_NAMESPACE="external-secrets"
ESO_SA="external-secrets"

echo "============================================"
echo " Vault Initialization & Configuration"
echo "============================================"

# Step 1: Wait for Vault pod to be running
echo ""
echo "[1/6] Waiting for Vault pod..."
kubectl -n ${VAULT_NAMESPACE} wait --for=condition=Ready pod/${VAULT_POD} --timeout=300s 2>/dev/null || true

# Step 2: Initialize Vault
echo ""
echo "[2/6] Initializing Vault..."
INIT_OUTPUT=$(kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- vault operator init \
  -key-shares=1 \
  -key-threshold=1 \
  -format=json 2>/dev/null || echo "ALREADY_INIT")

if [ "$INIT_OUTPUT" = "ALREADY_INIT" ]; then
  echo "  Vault is already initialized."
  echo "  If you need to unseal, run:"
  echo "    kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- vault operator unseal <UNSEAL_KEY>"
  echo ""
  echo "  Then re-run this script."
  echo ""
  read -p "  Is Vault already unsealed? (y/n): " UNSEALED
  if [ "$UNSEALED" != "y" ]; then
    echo "  Please unseal Vault first, then re-run this script."
    exit 1
  fi
else
  UNSEAL_KEY=$(echo "$INIT_OUTPUT" | jq -r '.unseal_keys_b64[0]')
  ROOT_TOKEN=$(echo "$INIT_OUTPUT" | jq -r '.root_token')

  echo ""
  echo "  ================================================"
  echo "  SAVE THESE CREDENTIALS SECURELY!"
  echo "  ================================================"
  echo "  Unseal Key: ${UNSEAL_KEY}"
  echo "  Root Token: ${ROOT_TOKEN}"
  echo "  ================================================"
  echo ""

  # Step 3: Unseal Vault
  echo "[3/6] Unsealing Vault..."
  kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- vault operator unseal "${UNSEAL_KEY}"
fi

# Export root token for subsequent commands (if we have it)
if [ -n "${ROOT_TOKEN:-}" ]; then
  export VAULT_TOKEN="${ROOT_TOKEN}"
else
  echo ""
  read -sp "  Enter Vault root token: " VAULT_TOKEN
  echo ""
  export VAULT_TOKEN
fi

# Step 4: Enable KV v2 secrets engine
echo ""
echo "[4/6] Enabling KV v2 secrets engine..."
kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault secrets enable -path=secret -version=2 kv 2>/dev/null || \
  echo "  KV v2 engine already enabled at 'secret/'"

# Step 5: Configure Kubernetes auth for External Secrets Operator
echo ""
echo "[5/6] Configuring Kubernetes auth method..."
kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault auth enable kubernetes 2>/dev/null || \
  echo "  Kubernetes auth already enabled."

kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc:443"

# Create policy for External Secrets Operator
kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  sh -c 'vault policy write external-secrets - <<POLICY
path "secret/data/*" {
  capabilities = ["read"]
}
path "secret/metadata/*" {
  capabilities = ["read", "list"]
}
POLICY'

# Create role for ESO service account
kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault write auth/kubernetes/role/external-secrets \
  bound_service_account_names="${ESO_SA}" \
  bound_service_account_namespaces="${ESO_NAMESPACE}" \
  policies=external-secrets \
  ttl=1h

echo ""
echo "[6/6] Storing initial secrets..."
echo ""

# Prompt for LLM API key
read -sp "  Enter your LLM API key (e.g., OpenAI sk-...): " LLM_API_KEY
echo ""

kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault kv put secret/kagent/llm api-key="${LLM_API_KEY}"

# Slack bot credentials
echo ""
read -sp "  Enter Slack Bot Token (xoxb-...): " SLACK_BOT_TOKEN
echo ""
read -sp "  Enter Slack App Token (xapp-...): " SLACK_APP_TOKEN
echo ""
read -p "  Enter Slack Team ID: " SLACK_TEAM_ID
read -p "  Enter Slack Channel IDs (comma-separated): " SLACK_CHANNEL_IDS

kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault kv put secret/slack \
    bot_token="${SLACK_BOT_TOKEN}" \
    app_token="${SLACK_APP_TOKEN}" \
    team_id="${SLACK_TEAM_ID}" \
    channel_ids="${SLACK_CHANNEL_IDS}"

# GitHub PAT
echo ""
read -sp "  Enter GitHub Personal Access Token: " GITHUB_TOKEN
echo ""

kubectl exec -n ${VAULT_NAMESPACE} ${VAULT_POD} -- \
  env VAULT_TOKEN="${VAULT_TOKEN}" \
  vault kv put secret/github token="${GITHUB_TOKEN}"

echo ""
echo "============================================"
echo " Vault Configuration Complete!"
echo "============================================"
echo ""
echo "Secrets stored:"
echo "  - secret/kagent/llm (api-key)"
echo "  - secret/slack (bot_token, app_token)"
echo "  - secret/github (token)"
echo ""
echo "To add more secrets later:"
echo "  kubectl exec -n vault vault-0 -- env VAULT_TOKEN=<token> vault kv put secret/<path> <key>=<value>"
echo ""
echo "Access Vault UI:"
echo "  kubectl port-forward -n vault svc/vault-ui 8200:8200"
echo "  Open http://localhost:8200"
echo ""
