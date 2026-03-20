#!/usr/bin/env bash
set -euo pipefail

IMAGE="docker.io/sebbycorp/kagent-slack-bot:latest"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Building ${IMAGE} (linux/amd64 + linux/arm64)..."
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag "${IMAGE}" \
  --push \
  "${SCRIPT_DIR}"

echo "Done: ${IMAGE}"
