#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# k8s-iceman Bootstrap Script
# Installs Argo CD and sets up the GitOps pipeline
# =============================================================================

REPO_URL="https://github.com/ProfessorSeb/k8s-iceman.git"
ARGOCD_VERSION="v2.14.11"
ARGOCD_NAMESPACE="argocd"

echo "============================================"
echo " k8s-iceman GitOps Bootstrap"
echo "============================================"

# Step 1: Install Argo CD
echo ""
echo "[1/3] Installing Argo CD ${ARGOCD_VERSION}..."
kubectl create namespace ${ARGOCD_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n ${ARGOCD_NAMESPACE} -f https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml

echo "[1/3] Waiting for Argo CD to be ready..."
kubectl -n ${ARGOCD_NAMESPACE} rollout status deployment argocd-server --timeout=300s
kubectl -n ${ARGOCD_NAMESPACE} rollout status deployment argocd-repo-server --timeout=300s
kubectl -n ${ARGOCD_NAMESPACE} rollout status deployment argocd-applicationset-controller --timeout=300s

# Step 2: Install Kubernetes Gateway API CRDs (required by Istio ambient + agentgateway)
echo ""
echo "[2/3] Installing Kubernetes Gateway API CRDs..."
kubectl get crd gateways.gateway.networking.k8s.io &>/dev/null || \
  kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml

# Step 3: Apply the root App of Apps
echo ""
echo "[3/3] Deploying root App of Apps..."
kubectl apply -f bootstrap/root-app.yaml

echo ""
echo "============================================"
echo " Bootstrap Complete!"
echo "============================================"
echo ""
echo "Argo CD admin password:"
echo "  kubectl -n ${ARGOCD_NAMESPACE} get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d && echo"
echo ""
echo "Port-forward Argo CD UI:"
echo "  kubectl port-forward svc/argocd-server -n ${ARGOCD_NAMESPACE} 8443:443"
echo ""
echo "Then visit: https://localhost:8443"
echo "Username: admin"
echo ""
