# nhdw/ollama-model: the Ollama runtime with the qwen2.5:3b and qwen2.5:14b weights
# pre-baked. It is the offline seed for the bare-metal, in-cluster GPU recipe only
# (baremetal/). The default external ollama container
# (0_external/llm/docker-compose.yml) does not use it; it runs stock
# ollama/ollama:0.6.8 with ../models mounted. build-images.sh does not build this
# by default; build it by hand for the bare-metal recipe (see 0_external/llm/README.md).
#
# 3b is the CPU default; 14b is used when a GPU is present (gpu-detect.sh points
# the gateway at it).
#
# Build context: 0_external/llm/models  (the host-pulled .ollama tree; regenerate with
#   docker run --rm -v <repo>/0_external/llm/models:/root/.ollama ollama/ollama:0.6.8 \
#     sh -c 'ollama serve & sleep 5; ollama pull qwen2.5:3b; ollama pull qwen2.5:14b'
# ). Build:  docker build -t nhdw/ollama-model:dev -f 0_external/llm/image/Dockerfile \
#            0_external/llm/models
#
# The weights land in /seed-models (not /root/.ollama): the in-cluster init
# container copies them into the llm-models PVC only when absent, so PVC state wins
# and the image stays a read-only seed.
FROM ollama/ollama:0.6.8
COPY models /seed-models
