# Bare-metal Linux in-cluster GPU recipe

Components for running the assistant's Ollama model on an NVIDIA GPU inside the k8s cluster, on a bare-metal Linux host. The default runtime is the external container in `../docker-compose.yml`, which works on every host including WSL2; use this recipe only where the LLM should run in-cluster.

## Why a stock k3d cluster cannot schedule GPU workloads

`k3d cluster create --gpus all` passes the GPU into the node container, but the stock `rancher/k3s` node image has no NVIDIA Container Toolkit, so:

1. k3s containerd has no `nvidia` runtime, and
2. the NVIDIA device plugin logs `Incompatible strategy detected auto` / `No devices found` and never advertises `nvidia.com/gpu`.

`k3d node create` has no `--gpus` flag (k3d v5.9.0), so GPU support always requires creating the cluster with `--gpus`.

On WSL2 the recipe does not help: CUDA cannot initialise inside a nested k3d node and pod (`cuInit` error 304, `CUDA_ERROR_OPERATING_SYSTEM`), and NVML is unsupported. Use the external container there.

## Files

- `Dockerfile.k3s-cuda`: a `rancher/k3s` node image with the NVIDIA Container Toolkit installed (`nhdw/k3s-cuda:dev`). k3s detects the toolkit at start-up and configures an `nvidia` containerd runtime and RuntimeClass. Build with the legacy builder, because the k3s root-filesystem `COPY` fails under BuildKit.
- `nvidia-device-plugin.yaml`: the NVIDIA device-plugin DaemonSet (`k8s-device-plugin:v0.16.2`) with `runtimeClassName: nvidia`. `FAIL_ON_INIT_ERROR=false` lets it degrade quietly on hosts without NVML. (`bootstrap.sh` applies the equivalent upstream manifest when a GPU flag is set.)

## Recipe

```bash
# 1. node image and baked-weights Ollama image
DOCKER_BUILDKIT=0 docker build -f 0_external/llm/baremetal/Dockerfile.k3s-cuda \
  -t nhdw/k3s-cuda:dev 0_external/llm/baremetal
docker build -t nhdw/ollama-model:dev -f 0_external/llm/image/Dockerfile 0_external/llm/models

# 2. create the cluster with the GPU and the custom node image, then add the device plugin
k3d cluster create nhdw --gpus all --image nhdw/k3s-cuda:dev ...
kubectl apply -f 0_external/llm/baremetal/nvidia-device-plugin.yaml
```

3. Deploy Ollama in the cluster. The repository ships no in-cluster LLM manifest; it needs a Deployment named for the gateway to reach (for example `nhdw-llm` on port 11434) using `nhdw/ollama-model`, an init container that copies `/seed-models` into an `llm-models` PVC when absent, and `resources.limits.nvidia.com/gpu: 1`.
4. Point the gateway's `OLLAMA_URL` at the in-cluster service (for example `http://nhdw-llm.nhdw.svc.cluster.local:11434`). `gpu-detect.sh` only selects the model; it does not grant GPU resources.

## Status

The components (node image, device plugin, baked image) have been validated individually. The full recipe has not been run end to end, because the reference host is WSL2, where the external path is used.
