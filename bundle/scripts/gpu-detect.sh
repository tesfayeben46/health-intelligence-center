#!/usr/bin/env bash
# gpu-detect.sh: choose the assistant's LLM model from what the external ollama
# container serves, and set it on the gateway.
#
# The LLM runs outside the cluster (0_external/llm/, a `docker compose` ollama
# stack the gateway reaches at host.k3d.internal:11434). On WSL2 a GPU cannot be
# used from inside a nested k3d pod (ollama cuInit returns error 304), so GPU
# inference runs in that external container with the host GPU passed through
# (sources-up.sh adds docker-compose.gpu.yml when a host GPU is present). This
# script tells the gateway which model to request:
#
#   14b weights served   NHDW_LLM_MODEL=qwen2.5:14b  (agentic tool use)
#   otherwise            NHDW_LLM_MODEL=qwen2.5:3b   (CPU-interactive)
#
# Both models ship in the external ollama's mounted weights (0_external/llm/models).
# Idempotent and best-effort: it never fails the boot, so the same bundle runs on
# CPU-only and GPU hosts. An in-cluster GPU setup for bare metal is in
# 0_external/llm/baremetal/.
set -uo pipefail
NS="${1:-nhdw}"

# Decide from what the external ollama actually serves. The agentic assistant
# needs qwen2.5:14b (3b cannot reliably chain tool calls), so prefer 14b whenever
# it is pulled. Probe /api/tags on the routes that reach ollama from the host
# (localhost) or from a container context (host.k3d.internal,
# host.docker.internal); fall back to 3b only when the 14b weights are absent.
MODEL="qwen2.5:14b"; picked=""
for url in "http://localhost:11434" "http://host.k3d.internal:11434" "http://host.docker.internal:11434"; do
  tags="$(curl -sf --max-time 4 "$url/api/tags" 2>/dev/null || true)"
  [ -z "$tags" ] && continue
  if echo "$tags" | grep -q 'qwen2.5:14b'; then MODEL="qwen2.5:14b"; picked="$url"; break; fi
  if echo "$tags" | grep -q 'qwen2.5:3b';  then MODEL="qwen2.5:3b";  picked="$url"; fi
done
if [ "$MODEL" = "qwen2.5:14b" ]; then
  echo "  ok: external ollama serves qwen2.5:14b${picked:+ ($picked)}: assistant -> 14b (agentic)"
else
  echo "  ! qwen2.5:14b not found on the host ollama, assistant -> ${MODEL} (fallback; pull 14b for agentic)"
fi
kubectl -n "$NS" set env deploy/nhdw-gateway NHDW_LLM_MODEL="$MODEL" >/dev/null 2>&1 \
  && echo "  ok: gateway NHDW_LLM_MODEL=$MODEL" \
  || echo "  ! could not set gateway model env (gateway not deployed yet?)"
