#!/usr/bin/env python3
"""Safe installer for a single CPU-only Ollama LFM3-VL Kubernetes workload."""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = "app.kubernetes.io/managed-by"
MANAGED = "kube.ollama_lfm3"
BASELINE = {("ServiceAccount", "default"), ("ConfigMap", "kube-root-ca.crt")}


def defaults():
    return {
        "namespace": "lfm3",
        "host": "lfm3.example.com",
        "ingress_class": "traefik",
        "model": "lfm3-vl:2b",
        "derived_model": "lfm3-vl-hermes:2b",
        "image": "ollama/ollama:latest",
        "storage_class": "local-path",
        "storage_gib": 10,
        "input_context_tokens": 16384,
        "output_tokens": 512,
        "cpu_request": "2",
        "memory_request": "4Gi",
        "memory_limit": "8Gi",
        "public_ingress": True,
        "token_file": "current_config/api-token",
    }


def config_path(args):
    return Path(args.config or os.environ.get("CONFIG_FILE", ROOT / "current_config" / "config.json"))


def load_config(args):
    p = config_path(args)
    if not p.is_file():
        raise ValueError(f"configuration missing: {p}; run ./configure.sh first")
    if p.stat().st_mode & 0o077:
        raise ValueError(f"configuration must be mode 0600: {p}")
    data = json.loads(p.read_text())
    unknown = set(data) - set(defaults())
    if unknown:
        raise ValueError("unknown configuration fields: " + ", ".join(sorted(unknown)))
    c = defaults(); c.update(data)
    validate(c, p)
    return c, p


def validate(c, config):
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", c["namespace"]):
        raise ValueError("namespace must be a DNS label")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", c["host"]) or "." not in c["host"]:
        raise ValueError("host must be a DNS hostname")
    if not re.fullmatch(r"[\w.-]+:[\w.-]+", c["model"]) or not re.fullmatch(r"[\w.-]+:[\w.-]+", c["derived_model"]):
        raise ValueError("model and derived_model must be explicit Ollama tags")
    if c["input_context_tokens"] < 2048 or c["input_context_tokens"] % 1024:
        raise ValueError("input_context_tokens must be a multiple of 1024 and >= 2048")
    if c["output_tokens"] < 128:
        raise ValueError("output_tokens must be >= 128")
    if c["storage_gib"] < 5:
        raise ValueError("storage_gib must be >= 5")
    token = resolve_token(c, config)
    if c["public_ingress"] and (not token.is_file() or len(token.read_text().strip()) < 32):
        raise ValueError("public ingress requires a token file containing at least 32 characters")


def resolve_token(c, config):
    p = Path(c["token_file"])
    return p if p.is_absolute() else config.parent / p.name


def usable_prompt_limit(runtime_ctx, num_keep=4):
    """Match Ollama llm/contextShiftPromptLimit, assuming default num_keep=4."""
    return runtime_ctx - max((runtime_ctx - num_keep) // 2, 1)


def runtime_context(c):
    # Need a full runtime window of ~2*desired prompt input due to Ollama's
    # context-shift safety logic; round for predictable KV cache sizing.
    return c["input_context_tokens"] * 2


def metadata(name, c, component):
    return {"name": name, "namespace": c["namespace"], "labels": {LABEL: MANAGED, "app.kubernetes.io/name": "ollama-lfm3", "app.kubernetes.io/component": component}}


def render(c, token):
    runtime = runtime_context(c)
    common = {LABEL: MANAGED, "app.kubernetes.io/name": "ollama-lfm3"}
    docs = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": c["namespace"], "labels": common}},
        {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": metadata("ollama-models", c, "runtime"), "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": c["storage_class"], "resources": {"requests": {"storage": f'{c["storage_gib"]}Gi'}}}},
        {"apiVersion": "v1", "kind": "Secret", "metadata": metadata("lfm3-api-token", c, "gateway"), "type": "Opaque", "stringData": {"token": token}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata("gateway-config", c, "gateway"), "data": {"default.conf.template": "server {\n  listen 8080;\n  client_max_body_size 20m;\n  location = /healthz { return 200 \\\"ok\\n\\\"; }\n  location / {\n    if ($http_authorization != \\\"Bearer ${LFM3_API_TOKEN}\\\") { add_header WWW-Authenticate \\\"Bearer\\\" always; return 401; }\n    proxy_pass http://ollama:11434;\n    proxy_read_timeout 1800s; proxy_send_timeout 1800s;\n  }\n}\n"}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata("ollama", c, "runtime"), "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": "ollama-lfm3"}}, "template": {"metadata": {"labels": {**common, "app": "ollama-lfm3"}}, "spec": {"automountServiceAccountToken": False, "containers": [{"name": "ollama", "image": c["image"], "command": ["/bin/sh", "-ec"], "args": ["ollama serve & p=$!; trap 'kill $p; wait $p || true' EXIT; until ollama list >/dev/null 2>&1; do sleep 1; done; ollama show \"$BASE_MODEL\" >/dev/null 2>&1 || ollama pull \"$BASE_MODEL\"; if ! ollama show \"$MODEL\" >/dev/null 2>&1; then printf 'FROM %s\\nPARAMETER num_ctx %s\\nPARAMETER num_predict %s\\n' \"$BASE_MODEL\" \"$RUNTIME_CONTEXT\" \"$OUTPUT_TOKENS\" >/tmp/Modelfile; ollama create \"$MODEL\" -f /tmp/Modelfile; fi; wait $p"], "env": [{"name": "MODEL", "value": c["derived_model"]}, {"name": "BASE_MODEL", "value": c["model"]}, {"name": "RUNTIME_CONTEXT", "value": str(runtime)}, {"name": "OUTPUT_TOKENS", "value": str(c["output_tokens"])}, {"name": "OLLAMA_HOST", "value": "0.0.0.0:11434"}, {"name": "OLLAMA_MODELS", "value": "/models"}, {"name": "OLLAMA_CONTEXT_LENGTH", "value": str(runtime)}, {"name": "OLLAMA_NUM_PARALLEL", "value": "1"}, {"name": "OLLAMA_LLM_LIBRARY", "value": "cpu"}, {"name": "OLLAMA_VULKAN", "value": "0"}], "ports": [{"name": "http", "containerPort": 11434}], "resources": {"requests": {"cpu": c["cpu_request"], "memory": c["memory_request"]}, "limits": {"memory": c["memory_limit"]}}, "startupProbe": {"tcpSocket": {"port": "http"}, "periodSeconds": 5, "failureThreshold": 180}, "readinessProbe": {"exec": {"command": ["/bin/sh", "-ec", "ollama show \\\"$MODEL\\\" >/dev/null"]}, "periodSeconds": 10}, "volumeMounts": [{"name": "models", "mountPath": "/models"}]}], "volumes": [{"name": "models", "persistentVolumeClaim": {"claimName": "ollama-models"}}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": metadata("ollama", c, "runtime"), "spec": {"type": "ClusterIP", "selector": {"app": "ollama-lfm3"}, "ports": [{"name": "http", "port": 11434, "targetPort": "http"}]}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata("lfm3-gateway", c, "gateway"), "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "lfm3-gateway"}}, "template": {"metadata": {"labels": {**common, "app": "lfm3-gateway"}}, "spec": {"automountServiceAccountToken": False, "containers": [{"name": "gateway", "image": "nginx:1.27-alpine", "ports": [{"name": "http", "containerPort": 8080}], "env": [{"name": "LFM3_API_TOKEN", "valueFrom": {"secretKeyRef": {"name": "lfm3-api-token", "key": "token"}}}], "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}}, "readinessProbe": {"httpGet": {"path": "/healthz", "port": "http"}}, "volumeMounts": [{"name": "config", "mountPath": "/etc/nginx/templates/default.conf.template", "subPath": "default.conf.template", "readOnly": True}]}], "volumes": [{"name": "config", "configMap": {"name": "gateway-config"}}]}}}},
        {"apiVersion": "v1", "kind": "Service", "metadata": metadata("lfm3-gateway", c, "gateway"), "spec": {"type": "ClusterIP", "selector": {"app": "lfm3-gateway"}, "ports": [{"name": "http", "port": 80, "targetPort": "http"}]}},
    ]
    if c["public_ingress"]:
        docs.append({"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {**metadata("lfm3", c, "gateway"), "annotations": {"traefik.ingress.kubernetes.io/router.entrypoints": "websecure", "traefik.ingress.kubernetes.io/router.tls": "true"}}, "spec": {"ingressClassName": c["ingress_class"], "rules": [{"host": c["host"], "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {"service": {"name": "lfm3-gateway", "port": {"number": 80}}}}]}}]}})
    return docs


def run(command, input_data=None, timeout=300):
    p = subprocess.run(command, input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or "command failed")
    return p.stdout


def kubectl(c, *args, data=None):
    return run(["kubectl", "--request-timeout=20s", *args], data)


def render_json(docs):
    return (json.dumps({"apiVersion": "v1", "kind": "List", "items": docs}, indent=2) + "\n").encode()


def ensure_owned(c):
    raw = kubectl(c, "get", "all,cm,secret,pvc,ingress", "-n", c["namespace"], "-o", "json", "--ignore-not-found")
    for item in json.loads(raw or b'{"items":[]}')["items"]:
        key = (item.get("kind"), item.get("metadata", {}).get("name"))
        labels = item.get("metadata", {}).get("labels", {})
        if key not in BASELINE and labels.get(LABEL) != MANAGED:
            raise RuntimeError(f"namespace {c['namespace']} contains foreign {key[0]}/{key[1]}; refusing adoption")


def cmd_configure(args):
    c = defaults()
    if args.from_answers:
        c.update(json.loads(Path(args.from_answers).read_text()))
    if args.namespace: c["namespace"] = args.namespace
    if args.host: c["host"] = args.host
    if args.input_context_tokens: c["input_context_tokens"] = args.input_context_tokens
    path = config_path(args); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = resolve_token(c, path); c["token_file"] = str(token)
    if not token.exists():
        token.write_text(secrets.token_urlsafe(48)); os.chmod(token, 0o600)
    validate(c, path)
    path.write_text(json.dumps(c, indent=2) + "\n"); os.chmod(path, 0o600)
    print(f"saved private config: {path}")
    print(f"runtime context: {runtime_context(c)}; safe usable prompt limit: {usable_prompt_limit(runtime_context(c))}")
    print(f"token file: {token}")


def cmd_install(args):
    c, path = load_config(args); token = resolve_token(c, path).read_text().strip(); docs = render(c, token); rendered = render_json(docs)
    artifact = ROOT / "artifacts" / "rendered.json"; artifact.parent.mkdir(exist_ok=True); artifact.write_bytes(rendered); os.chmod(artifact, 0o600)
    if args.render_only:
        print(artifact); return
    existing = kubectl(c, "get", "namespace", c["namespace"], "--ignore-not-found", "-o", "name")
    if existing: ensure_owned(c)
    kubectl(c, "apply", "--server-side", "--field-manager=kube-ollama-lfm3", "--dry-run=server", "-f", "-", data=rendered)
    kubectl(c, "apply", "--server-side", "--field-manager=kube-ollama-lfm3", "-f", "-", data=rendered)
    kubectl(c, "-n", c["namespace"], "rollout", "status", "deployment/lfm3-gateway", "--timeout=5m")
    kubectl(c, "-n", c["namespace"], "rollout", "status", "deployment/ollama", "--timeout=17m")
    print("installed; run ./doctor.sh for endpoint and context verification")


def cmd_doctor(args):
    c, path = load_config(args); ns = c["namespace"]
    ensure_owned(c)
    kubectl(c, "-n", ns, "get", "deploy,pods,pvc,svc,ingress")
    logs = kubectl(c, "-n", ns, "logs", "deployment/ollama", "--tail=150").decode(errors="replace")
    expected = runtime_context(c)
    if f"OLLAMA_CONTEXT_LENGTH:{expected}" not in logs or "library=cpu" not in logs:
        raise RuntimeError("Ollama did not confirm expected context and CPU library in startup logs")
    ps = kubectl(c, "-n", ns, "exec", "deployment/ollama", "--", "ollama", "ps").decode()
    if str(expected) not in ps:
        raise RuntimeError("ollama ps does not show expected runtime context")
    print(f"PASS CPU library and runtime context {expected}; usable prompt ceiling is about {usable_prompt_limit(expected)} tokens")
    print("WARNING: Ollama may silently context-shift oversized input. Keep client prompts below this ceiling or add a token-counting/rejecting proxy.")


def cmd_maintain(args):
    c, _ = load_config(args)
    if args.action == "status":
        kubectl(c, "-n", c["namespace"], "get", "deploy,pods,pvc,svc,ingress", "-o", "wide")
    elif args.action == "restart":
        ensure_owned(c); kubectl(c, "-n", c["namespace"], "rollout", "restart", "deployment/ollama"); kubectl(c, "-n", c["namespace"], "rollout", "status", "deployment/ollama", "--timeout=17m")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    conf = sub.add_parser("configure"); conf.add_argument("--config"); conf.add_argument("--from-answers"); conf.add_argument("--namespace"); conf.add_argument("--host"); conf.add_argument("--input-context-tokens", type=int)
    ins = sub.add_parser("install"); ins.add_argument("--config"); ins.add_argument("--render-only", action="store_true")
    doc = sub.add_parser("doctor"); doc.add_argument("--config")
    maint = sub.add_parser("maintain"); maint.add_argument("action", choices=("status", "restart")); maint.add_argument("--config")
    a = p.parse_args()
    try:
        {"configure": cmd_configure, "install": cmd_install, "doctor": cmd_doctor, "maintain": cmd_maintain}[a.command](a)
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); raise SystemExit(2)

if __name__ == "__main__": main()
