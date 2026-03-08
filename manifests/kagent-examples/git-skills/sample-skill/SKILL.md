---
name: kubernetes-troubleshooting
description: Step-by-step guide for diagnosing common Kubernetes issues
---

# Kubernetes Troubleshooting Guide

## Pod CrashLoopBackOff

When a pod is stuck in CrashLoopBackOff:

1. **Check pod logs** for the current and previous container:
   ```
   kubectl logs <pod-name> --previous
   kubectl logs <pod-name>
   ```

2. **Check pod events** for scheduling or image pull issues:
   ```
   kubectl describe pod <pod-name>
   ```

3. **Common causes:**
   - Application crash on startup (missing config, failed DB connection)
   - OOMKilled (check resource limits)
   - Liveness probe failure (check probe configuration)
   - Missing ConfigMap or Secret references

## Pod Pending

When a pod stays in Pending state:

1. **Check events** for scheduling failures:
   ```
   kubectl describe pod <pod-name>
   ```

2. **Common causes:**
   - Insufficient cluster resources (CPU/memory)
   - Node affinity/taint mismatch
   - PVC binding failure
   - Image pull backoff

## High Latency Investigation

1. Check pod resource usage vs limits
2. Query Prometheus for request latency: `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))`
3. Check for network policy restrictions
4. Verify HPA scaling behavior
5. Check for noisy neighbors on shared nodes
