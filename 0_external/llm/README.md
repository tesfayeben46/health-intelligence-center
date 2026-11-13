# External LLM (Ollama)

The model runtime behind the internal dashboard assistant. Ollama serves Qwen 2.5 outside the k8s cluster, as a Docker Compose stack, for the same reason as every other `0_external/` system: in production a model server is a backing resource run outside the warehouse. The gateway reaches it at `http://host.k3d.internal:11434`, and the browser never talks to Ollama directly.

## Layout

| Path | Contents |
|---|---|
| `docker-compose.yml` | Ollama stack (`ollama/ollama:0.6.8`, container `nhdw-ollama`); CPU by default |
| `docker-compose.gpu.yml` | Additive overlay that reserves the NVIDIA GPU |
| `models/` | Ollama home mounted at `/root/.ollama`: the weight store (gitignored; shipped in a bundle) |
| `image/` | Builder for the baked-weights image `nhdw/ollama-model` (bare-metal recipe only) |
| `baremetal/` | In-cluster GPU recipe for bare-metal Linux (custom k3s node image and device plugin) |

## Models

| Ollama serves | Model | Chosen by |
|---|---|---|
| the 14b weights | `qwen2.5:14b` | `bundle/scripts/gpu-detect.sh` sets `NHDW_LLM_MODEL` on the gateway |
| only the 3b weights | `qwen2.5:3b` | same |

The choice follows the weights the host Ollama serves, not GPU presence: a CPU-only host that has the 14b weights also gets 14b. A GPU host pulls 14b at bring-up; a CPU-only host does not.

Both are public weights. On GPU, 14b runs at roughly 20 times its CPU speed; on CPU it takes minutes per answer, so the CPU default is 3b. `OLLAMA_KEEP_ALIVE=24h` keeps the loaded model resident between requests.

## Bring-up

`bootstrap.sh` brings the stack up through `bundle/scripts/sources-up.sh` (`up_llm`), which:

1. adds `docker-compose.gpu.yml` when `nvidia-smi` reports a GPU;
2. starts the stack on `0.0.0.0:11434` and waits for `/api/tags`;
3. pulls `qwen2.5:3b` (and `qwen2.5:14b` on a GPU host) only if it is missing from `models/`;
4. preloads the selected model so the first request is not a cold load.

Manually:

```bash
docker compose -f 0_external/llm/docker-compose.yml up -d                                          # CPU
docker compose -f 0_external/llm/docker-compose.yml -f 0_external/llm/docker-compose.gpu.yml up -d   # GPU (NVIDIA Container Toolkit)
curl http://127.0.0.1:11434/api/tags
```

`bundle/scripts/sources-up.sh down` stops it with the other external stacks. The weights are a bind mount, so `down --wipe` does not remove them.

## Offline use

The image `ollama/ollama:0.6.8` is in the public image list of `bundle/scripts/cache-images.sh`, which saves it to `bundle/images/` for `docker load` at cold boot. A bundle ships `models/` with both models pulled, so bring-up needs no network. On a fresh clone `models/` is absent and the first bring-up pulls the weights.

## Why the LLM runs outside the cluster

On WSL2, CUDA cannot initialise inside a nested k3d node and pod (`cuInit` returns error 304, `CUDA_ERROR_OPERATING_SYSTEM`), and the k8s device plugin cannot enumerate GPUs (NVML "Not Supported"). The GPU works in a container run directly on the host Docker daemon with the GPU passed through, which is what this stack does. Ollama 0.6.8 supports Blackwell (`sm_120`) GPUs and offloads all layers of qwen2.5:14b.

On bare-metal Linux the LLM can run inside the cluster instead; see `baremetal/README.md`. That path is an alternative, not the default.
