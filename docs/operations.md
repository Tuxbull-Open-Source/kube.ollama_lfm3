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
