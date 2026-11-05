# kube.ollama_lfm3

Deploy **LiquidAI LFM3-VL-2B** through Ollama on Kubernetes/k3s with CPU-only
inference, a persistent model cache, a token-protected OpenAI-compatible public
endpoint, and a context-limit guard designed for agent demos.

This is a small, single-node-friendly demo installer, **not** a production HA
model service and not a general replacement for GPU inference.

## Quick start

```bash
git clone https://github.com/Tuxbull-Open-Source/kube.ollama_lfm3.git
cd kube.ollama_lfm3
./configure.sh --namespace lfm3 --host lfm3.example.com
./install.sh --render-only
# Inspect artifacts/rendered.json. It includes a Secret: never commit or share it.
./install.sh
./doctor.sh
./maintain.sh status
```

Retrieve the generated Bearer token only on the operator machine:

```bash
cat current_config/api-token
```

Call the public endpoint:

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
- private Service `ollama.<namespace>.svc:11434`
- Nginx gateway that enforces `Authorization: Bearer …`
- Traefik TLS Ingress to the gateway; DNS, public TLS certificate and Traefik
  remain operator-owned prerequisites

The token is created mode 0600 beside the private configuration. It is applied
into the namespaced Kubernetes Secret but never printed by the installer.

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
- `public_ingress`: defaults true; set false for internal-only Service access
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
- No Kubernetes-level authentication is added to raw Ollama; do not publish the
  `ollama` Service directly. Only expose the token gateway.
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
