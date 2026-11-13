#!/usr/bin/env bash
# fetch-weights.sh: populate the 0_external/llm/models weight tree with qwen2.5:7b.
# The tree is both the mount for the default external ollama container and the
# build context for the bare-metal nhdw/ollama-model image. The models the
# platform uses are qwen2.5:3b and qwen2.5:14b; refresh those with
# `ollama pull qwen2.5:3b && ollama pull qwen2.5:14b` into the mounted models dir.
# This script covers only the 7b model, with a GGUF fallback for restricted networks.
#
# Strategy (network needed once, here only):
#   1. `ollama pull qwen2.5:7b` (registry.ollama.ai -> R2 blobs).
#   2. If R2 is unreachable: download the same model as a GGUF from ModelScope
#      (Hugging Face as backup) and `ollama create` it with the official
#      template and system prompt (Modelfile in this dir) under the same name.
# Afterwards: build-images.sh --only=ollama-model builds nhdw/ollama-model:dev and
# cache-images.sh saves its bundle tar; the k8s init container seeds the PVC from
# the image with no network at boot.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OLLAMA_DIR="$REPO/0_external/llm/models"
GGUF_NAME="qwen2.5-7b-instruct-q4_k_m.gguf"
MS_URL="https://modelscope.cn/models/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/master/$GGUF_NAME"
HF_URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/$GGUF_NAME"
CONTAINER=ollama-weights

mkdir -p "$OLLAMA_DIR/gguf"

run_ollama() { # start a throwaway ollama with the bundle dir mounted as its home
  docker rm -f "$CONTAINER" >/dev/null 2>&1
  docker run -d --name "$CONTAINER" -v "$OLLAMA_DIR":/root/.ollama ollama/ollama:0.6.8 >/dev/null
  for i in $(seq 1 12); do docker exec "$CONTAINER" ollama list >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}

have_model() { docker exec "$CONTAINER" ollama list 2>/dev/null | grep -q "qwen2.5:7b"; }

echo "== ollama weights -> $OLLAMA_DIR =="
run_ollama || { echo "ERROR: could not start ollama container"; exit 1; }
if have_model; then
  echo "ok: qwen2.5:7b already present: nothing to do"
  docker rm -f "$CONTAINER" >/dev/null; exit 0
fi

echo "▸ attempt 1: official ollama pull (fails fast if R2 is blackholed)"
if timeout 300 docker exec "$CONTAINER" ollama pull qwen2.5:7b >/dev/null 2>&1 && have_model; then
  echo "ok: pulled via registry.ollama.ai"
  docker rm -f "$CONTAINER" >/dev/null; exit 0
fi

echo "▸ attempt 2: GGUF from ModelScope (HF fallback) + ollama create with the official Modelfile"
GGUF="$OLLAMA_DIR/gguf/$GGUF_NAME"
for url in "$MS_URL" "$HF_URL"; do
  echo "  downloading $(echo "$url" | cut -d/ -f3) ..."
  for a in 1 2 3 4 5; do
    curl -sL -C - --retry 8 --retry-delay 5 -o "$GGUF" "$url" && break
    sleep 5
  done
  # sanity: a real GGUF starts with magic "GGUF"
  [ -f "$GGUF" ] && [ "$(head -c4 "$GGUF")" = "GGUF" ] && break
done
[ "$(head -c4 "$GGUF" 2>/dev/null)" = "GGUF" ] || { echo "ERROR: GGUF download failed from all mirrors"; exit 1; }

docker cp "$(dirname "${BASH_SOURCE[0]}")/Modelfile" "$CONTAINER":/root/.ollama/gguf/Modelfile
docker exec "$CONTAINER" ollama create qwen2.5:7b -f /root/.ollama/gguf/Modelfile \
  && have_model && echo "ok: created qwen2.5:7b from GGUF (official template/system)" \
  || { echo "ERROR: ollama create failed"; exit 1; }
docker rm -f "$CONTAINER" >/dev/null

# The GGUF is now redundant (blobs live under models/); remove it to keep the
# docker build context and the image small.
rm -f "$GGUF"
echo "== done: the external ollama mounts this tree directly (default path)."
echo "   For the bare-metal image (Recipe A): docker build -t nhdw/ollama-model:dev \\"
echo "     -f 0_external/llm/image/Dockerfile 0_external/llm/models =="
