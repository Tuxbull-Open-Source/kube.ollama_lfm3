# kube.ollama_lfm3

Deploy **LiquidAI LFM3-VL-2B** through Ollama on Kubernetes/k3s with CPU-only
inference, a persistent model cache, optional Bearer-token-protected public
access, and a context-limit guard designed for agent demos.

This is a small, single-node-friendly demo installer, **not** a production HA
model service and not a general replacement for GPU inference.

## Quick start: choose the exposure mode first

Both modes install one CPU-only Ollama runtime with a persistent model PVC. The
choice controls how clients reach it.

### Option 1 — internal ClusterIP Service (default, leanest)

Use this when the client runs inside the cluster, or when an existing
operator-managed access layer already protects the service. It creates exactly
**one runtime container**: Ollama. It creates no token, gateway or Ingress.

```bash
git clone https://github.com/Tuxbull-Open-Source/kube.ollama_lfm3.git
cd kube.ollama_lfm3
./configure.sh --namespace lfm3 --exposure service
./install.sh --render-only
./install.sh
./doctor.sh
./maintain.sh status
```

Connect an in-cluster client to:

```text
http://ollama.lfm3.svc:11434/v1
```

**Note:** Ollama's local server ignores client API-key/Bearer values. Do not
expose this Service directly to the Internet.

### Option 2 — public Ingress with Bearer token

Use this when OpenWebUI or another external OpenAI-compatible client needs a
public HTTPS endpoint and this installer must enforce a Bearer token. It creates
**two runtime containers** in separate Deployments: Ollama plus a small Nginx
gateway. The gateway validates the token before forwarding requests to Ollama.

```bash
git clone https://github.com/Tuxbull-Open-Source/kube.ollama_lfm3.git
cd kube.ollama_lfm3
./configure.sh \
  --namespace lfm3 \
  --host lfm3.example.com \
  --exposure bearer_ingress
./install.sh --render-only
./install.sh
./doctor.sh
./maintain.sh status
```

The generated token is private and is never printed by the installer:

```bash
cat current_config/api-token
```

Configure the client with:

```text
Base URL: https://lfm3.example.com/v1
Model:    lfm3-vl-hermes:2b
API key:  <contents of current_config/api-token>
```

**Note:** This assumes that public DNS, TLS termination and a Traefik Ingress
class already exist. The installer does not create certificates, DNS records or
an Ingress controller.

## Exposure modes

The configurator asks once which mode to use. For automation, set `exposure`
in the private answers JSON or pass `--exposure`.

| Mode | Installed runtime containers | Endpoint | Authentication |
|---|---:|---|---|
| `service` (default) | 1: Ollama | `http://ollama.NAMESPACE.svc:11434/v1` | None — use only within the cluster or behind your existing access layer |
| `bearer_ingress` | 2: Ollama + Nginx gateway | `https://HOSTNAME/v1` | Generated Bearer token, enforced before Ollama |

`service` creates no Ingress, Secret, ConfigMap, gateway Deployment, or
gateway Service. It is the lean mode for internal clients. Ollama's local
server accepts OpenAI-compatible API requests but ignores a client API key, so
never expose this mode directly to the Internet.

`bearer_ingress` is required when OpenWebUI or another external client needs a
Bearer token. It creates the second, small Nginx gateway container because
Ollama itself does not validate the local API key.

Retrieve the generated Bearer token only in `bearer_ingress` mode:

```bash
cat current_config/api-token
```

Call the protected public endpoint:

```bash
curl -fsS https://lfm3.example.com/v1/models \
  -H "Authorization: Bearer $(cat current_config/api-token)"
```

Use the exposed model name `lfm3-vl-hermes:2b` with an OpenAI-compatible
client. Internally it derives from `lfm3-vl:2b` and persists the parameters
across pod restarts.

## What it installs

- exclusive installer-owned namespace (default `lfm3`)
- Ollama Deployment, one replica, `Recreate`, no Kubernetes service-account
  token, no GPU resource/device request
- `local-path` 10 GiB RWO PVC by default
- private Service `ollama.NAMESPACE.svc:11434`
- Optional Nginx gateway that enforces Bearer authentication
- Optional Traefik TLS Ingress to the gateway; DNS, public TLS certificate and
  Traefik remain operator-owned prerequisites

In `bearer_ingress` mode, the token is created mode 0600 beside the private
configuration. It is applied into the namespaced Kubernetes Secret but never
printed by the installer.

## Context is deliberately explicit

Ollama's configured `num_ctx` is **not** a safe usable one-message input
budget. Its source implements a context shift when a prompt exceeds the full
buffer. It preserves the initial tokens and drops roughly half of the remaining
buffer before generation:

```go
return numCtx - max((numCtx-numKeep)/2, 1)
```

With default `num_keep=4`, an allocated 32,768-token runtime has an effective
single-message ceiling of about **16,386** tokens. This was reproduced on
Ollama 0.40.14 with LFM3-VL and is tracked upstream:

- [Ollama source](https://github.com/ollama/ollama/blob/main/llm/llama_server.go)
- [upstream issue #17427](https://github.com/ollama/ollama/issues/17427)
- [silent truncation issue #14259](https://github.com/ollama/ollama/issues/14259)
- [OpenAI API context discussion #2677](https://github.com/ollama/ollama/issues/2677)

The configuration accepts `input_context_tokens`, not a misleading raw
`num_ctx`. It derives runtime context as `2 × input_context_tokens`:

| Desired safe prompt budget | Runtime `num_ctx` | Approx. shift limit |
|---:|---:|---:|
| 16,384 | 32,768 | 16,386 |
| 32,768 | 65,536 | 32,770 |
| **64,000 (default and model maximum)** | **128,000** | **64,002** |

LFM3-VL-2B advertises a 128K input-plus-output model context. The installer
therefore caps `input_context_tokens` at 64,000 and derives `num_ctx=128000`:
this is the highest runtime context supported by the model while accounting for
Ollama's shift behavior. It does **not** make a 128K one-message prompt safe.

Increasing runtime context increases KV-cache memory. In the original CPU-only
LFM3 validation, Q4_K_M at 32K runtime measured about 2.59 GiB current and
2.90 GiB peak cgroup memory with no OOM. The 128K setting starts with a 6 GiB
container limit, not 12 GiB, but that is a testable starting boundary rather
than a capacity guarantee. Node memory pressure, concurrent workloads, image
size, vision input, and Ollama version all affect the result. Review free node
memory, capture cgroup `memory.peak`, and revert before production use if the
limit is approached or the node reports memory pressure.

`./doctor.sh` verifies CPU-library startup logs and loaded runtime context. It
also reports the calculated usable ceiling. It cannot make Ollama return a
client-visible truncation flag; callers needing strict no-loss behavior must
count tokens before sending or place a rejecting token-counting proxy in front
of Ollama.

## Configuration

The configurator creates `current_config/config.json` and
`current_config/api-token`, both mode 0600. To automate, copy and edit the
sanitized example, keep it outside Git, and run:

```bash
chmod 600 /secure/lfm3.json
./configure.sh --from-answers /secure/lfm3.json
./install.sh --config /secure/lfm3.json --render-only
./install.sh --config /secure/lfm3.json
```

Important fields:

- `input_context_tokens`: safe usable prompt budget; default and maximum 64000
  (creates `num_ctx=128000`)
- `output_tokens`: derived model `num_predict`; default 512
- `memory_limit`: default 6Gi; validate actual cgroup peak before increasing it
- `exposure`: `service` (default, one container) or `bearer_ingress` (two
  containers plus public TLS Ingress and generated token)
- `image`: pin a tested digest after validation; `latest` is only an initial
  convenience default

## Requirements and ownership

- Python 3.9+, Bash, `kubectl`, and a kubeconfig pointing to the intended
  cluster. The installer installs no host packages.
- Ready nodes, DNS, dynamic `ReadWriteOnce` storage, an existing Traefik ingress
  class, public DNS/TLS termination for the chosen hostname, and registry/Ollama
  model-download egress.
- The namespace must be new or contain only objects labelled
  `app.kubernetes.io/managed-by=kube.ollama_lfm3`, plus Kubernetes baseline
  objects. The installer refuses to adopt foreign resources.
- No Kubernetes-level authentication is added to raw Ollama. Do not publish
  the `ollama` Service directly; use `bearer_ingress` when this installer must
  provide public exposure, or put the service behind an independently operated
  access layer.
- LFM3-VL-2B supports vision and tools in Ollama but Liquid AI does not
  recommend it for coding, complex reasoning or long multi-turn conversations.
  Position it as a bounded demo model.

## Operations

```bash
./maintain.sh status
./maintain.sh restart
./doctor.sh
```

`restart` is a mutation and should be performed under your operating change
process. Model download persistence is supplied by the PVC. There is no
uninstall command because PVC deletion is destructive; delete the namespace
only after a separate authorization and backup/recovery decision.

## Development checks

```bash
bash -n configure.sh install.sh doctor.sh maintain.sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
python3 scripts/cli.py --help
git diff --check
```

## License

MIT. See [LICENSE](LICENSE).
