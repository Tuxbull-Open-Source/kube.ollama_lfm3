# Operations

## Preflight

Run these commands against the intended cluster before a real install:

```bash
kubectl config current-context
kubectl get nodes -o wide
kubectl top nodes
kubectl get storageclass
kubectl get ingressclass
kubectl get namespace lfm3 --ignore-not-found
```

Check the public hostname resolves to the ingress endpoint and that TLS is
provided by the operator's existing controller or external proxy. This project
sets Traefik `websecure` and TLS routing annotations, but intentionally does
not create a certificate, ACME issuer, DNS record, or cluster-wide controller.

This prerequisite applies only to `bearer_ingress`. The default `service` mode
has no public hostname or Ingress: access it from the cluster at
`http://ollama.<namespace>.svc:11434/v1`, or add a separately operated access
layer. Do not expose a service-mode Ollama Service to the public Internet:
Ollama ignores local client API-key values.

## Exposure migration

Old configurations with `public_ingress: true` are accepted as
`exposure: bearer_ingress`; `false` maps to `service`. New configurations use
only `exposure`.

Changing a live, installer-owned namespace from `bearer_ingress` to `service`
does not automatically delete the old gateway/Ingress objects. Review and
remove those labelled resources through your Kubernetes change process after
confirming clients use the internal Service. This prevents an exposure-reducing
reconfiguration from silently deleting a route that may still be in use.

## Rollback

The installer does not delete PVCs automatically. Before changing a working
configuration, save a namespaced resource export and validate your backup
strategy. To roll back only a Deployment revision, use your Kubernetes change
procedure; the Deployment uses `Recreate`, so a rollback causes a short
inference interruption. Do not delete the namespace as a generic rollback: the
storage class may delete the persistent model cache.

## Context evidence

Run `./doctor.sh` after every context change. It confirms the server-startup
`OLLAMA_CONTEXT_LENGTH` and CPU library, then checks the loaded model through
`ollama ps`. For an oversized request, inspect server logs for:

```text
truncating input prompt limit=<n> prompt=<n> keep=<n> new=<n>
```

A normal HTTP 200 does not mean the whole prompt was processed. Record the
client-estimated prompt size and returned `usage.prompt_tokens`; reject or
chunk inputs before the calculated shift ceiling.

## 128K runtime sizing

The default configuration requests LFM3-VL-2B's maximum **128,000-token
runtime context**, derived from a safe `input_context_tokens` setting of
64,000. This is intentionally not a claim that a single API request may carry
128K tokens: the upstream context-shift logic lowers the safe one-message
limit to roughly 64,002 tokens.

Start with the provided 6 GiB Ollama memory limit. It is based on a CPU-only
Q4_K_M 32K-runtime measurement of approximately 2.59 GiB current / 2.90 GiB
peak cgroup memory without OOM; it is not a promise that every cluster can
allocate 128K beside its other workloads. Before accepting the deployment:

```bash
kubectl top nodes
kubectl -n <namespace> exec deployment/ollama -- sh -c '
  cat /sys/fs/cgroup/memory.current
  cat /sys/fs/cgroup/memory.peak
  cat /sys/fs/cgroup/memory.events
'
kubectl -n <namespace> get events --sort-by=.lastTimestamp
```

Test a controlled 64K fact-recall prompt while sampling `kubectl top pod`. If
`memory.events` reports `oom`, `oom_kill`, or `max`, if the Pod restarts, or if
the node reports MemoryPressure, restore the prior lower context configuration
and investigate capacity. Do not increase memory limits merely to hide node
pressure.
