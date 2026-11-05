# Changelog

## Unreleased

- Set the default LFM3 runtime context to its 128K-token model maximum.
- Treat 64K tokens as the maximum safe single-prompt input budget because
  Ollama context shifting reserves roughly half of the runtime context.
- Set the initial memory limit to 6 GiB, based on observed LFM3 Q4_K_M CPU
  measurements; require operator capacity validation before installation.

## 0.1.0

- Initial CPU-only Ollama LFM3-VL Kubernetes installer.
- Token-protected public OpenAI-compatible gateway.
- Persistent derived model configuration.
- Explicit usable-context calculation based on Ollama context-shift source code.
