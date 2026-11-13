# nhdw/ollama-model: baked-weights image builder

Builds `nhdw/ollama-model`, the stock Ollama runtime with the `qwen2.5:3b` and `qwen2.5:14b` weights copied in, so an in-cluster deployment can seed its model volume offline.

This image is used only by the bare-metal in-cluster recipe (`../baremetal/`). The default external container (`../docker-compose.yml`) mounts `../models` directly and needs no custom image; `bundle/scripts/build-images.sh` does not build this one by default.

## Files

- `Dockerfile`: `FROM ollama/ollama:0.6.8` plus `COPY models /seed-models`. The build context is `../models`. Weights land in `/seed-models`, not `/root/.ollama`, so an init container can copy them into a PVC only when absent and the image stays a read-only seed.
- `fetch-weights.sh`: populates `../models` with `qwen2.5:7b`, first through `ollama pull` and, when the registry's blob store is unreachable, from a GGUF on ModelScope or Hugging Face. The models the platform uses (3b and 14b) are refreshed with `ollama pull` (see below).
- `Modelfile`: the official qwen2.5 template and system prompt, used by `fetch-weights.sh` to recreate `qwen2.5:7b` from the GGUF under the registry name.

## Build

```bash
# refresh the weights the platform uses
docker run --rm -v "$PWD/0_external/llm/models:/root/.ollama" ollama/ollama:0.6.8 \
  sh -c 'ollama serve & sleep 5; ollama pull qwen2.5:3b; ollama pull qwen2.5:14b'

# build the image (context = the weight store)
docker build -t nhdw/ollama-model:dev -f 0_external/llm/image/Dockerfile 0_external/llm/models
```
