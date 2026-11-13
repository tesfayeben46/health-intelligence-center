#!/usr/bin/env bash
# bootstrap.sh: the canonical entry point. Cold-boots the whole platform into a
# k3d Kubernetes cluster and seeds it to a usable state.
#
# Data tiers (sources, lake, vault, marts): bring up and seed the external
# source replicas (sources-up.sh), then pull them through the dlt ingestors and
# run the dbt pipeline (source -> lake -> ClickHouse -> bridge -> vault -> marts).
# Only the sources are seeded; everything downstream is produced by the
# pipeline. The optional --fast cache is the single exception (see below).
#
# App and security state (ops DB, RBAC model, workshop groups, Keycloak realms)
# cannot be derived by the pipeline, so it is always seeded from files.
#
# Images (no registry): build or load from the offline cache, then
# `k3d image import` the nhdw/*:dev images; the dev overlay remaps the manifests'
# registry names to those local images with imagePullPolicy: IfNotPresent.
#
# Usage:
#   bootstrap.sh                       # full rebuild: sources -> ingest -> pipeline
#   bootstrap.sh --rebuild-images      # force a rebuild of the nhdw images
#   bootstrap.sh --offline             # load images from the offline cache tars
#   bootstrap.sh teardown              # delete cluster + stop sources (clean slate)
#   bootstrap.sh teardown --keep-cluster   # just `kubectl delete -k` the overlay
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS="$REPO/bundle/scripts"
OVERLAY="$REPO/1_infrastructure/k8s/overlays/dev"
CLUSTER=nhdw
NS=nhdw
export PATH="$HOME/.local/bin:$PATH"

# Country profile for this boot. The profile's data layer decides which entity
# model the generators read, and they resolve it themselves; this block makes
# the choice explicit in the log, because a boot that seeded the wrong country
# is expensive to discover later.
NHDW_PROFILE="${NHDW_PROFILE:-et-v3}"
export NHDW_PROFILE
if [ -z "${NHDW_ENTITY_BUILD:-}" ] && [ "$NHDW_PROFILE" != "et-v3" ]; then
  _model=$(sed -n "s|^ *entity_model: *||p" "$REPO/bundle/profiles/$NHDW_PROFILE/profile.yaml" 2>/dev/null | head -1)
  if [ -n "$_model" ]; then
    _stem=$(basename "$_model" .yaml)
    [ "$_stem" != "entity-model" ] \
      && export NHDW_ENTITY_BUILD="$REPO/bundle/seeds/entity-model/_build-$_stem"
  fi
fi
echo "▸ profile: $NHDW_PROFILE   entity build: ${NHDW_ENTITY_BUILD:-<default _build>}"

# Secrets parity. The local, gitignored secrets.yaml drifts when the template
# gains a key for a new component. An empty env var then aborts postgres initdb
# at that script, the container restarts half-initialised, and the failure
# surfaces much later as a missing schema. Checking key parity here names the
# missing key before anything runs.
if [ -f "$OVERLAY/secrets.yaml" ] && [ -f "$OVERLAY/secrets.yaml.template" ]; then
  MISSING_KEYS=$(python3 - "$OVERLAY" <<'PYEOF'
import re, sys, pathlib
o = pathlib.Path(sys.argv[1])
keys = lambda f: set(re.findall(r'^[ ]{2}([A-Z][A-Z0-9_]+):', f.read_text(), re.M))
print('\n'.join(sorted(keys(o / 'secrets.yaml.template') - keys(o / 'secrets.yaml'))))
PYEOF
)
  if [ -n "$MISSING_KEYS" ]; then
    echo "ERROR: secrets.yaml is missing keys the template declares:"
    echo "$MISSING_KEYS" | sed 's/^/    /'
    echo "  Copy each from secrets.yaml.template and set a value. Refusing to"
    echo "  boot: an empty secret aborts postgres init midway, which is worse."
    exit 1
  fi
  echo "▸ secrets: key parity with the template: OK"
fi

REBUILD_IMAGES=0
OFFLINE=0
case "${1:-}" in
  teardown)
    shift
    if [ "${1:-}" = "--keep-cluster" ]; then
      echo "▸ deleting the dev overlay (keeping cluster ${CLUSTER})"
      # Mirror the apply path: apply pipes `kustomize build
      # --load-restrictor=LoadRestrictionsNone` into kubectl, because the base's
      # configMapGenerator reaches files outside the kustomize root (jupyterhub
      # profiles, init SQL). `kubectl delete -k` enforces the default
      # restrictions and refuses the whole overlay, deleting nothing. Use the
      # same builder in both directions.
      kustomize build --load-restrictor=LoadRestrictionsNone "$OVERLAY" \
        | kubectl delete --ignore-not-found -f - 2>&1 | tail -5
      # StatefulSet volumes come from volumeClaimTemplates, so they are not in
      # the manifest stream and survive the delete above. A half-initialised
      # postgres volume left behind would break the next boot.
      kubectl -n "$NS" delete pvc --all --ignore-not-found --timeout=120s 2>&1 | tail -3
      # A PVC whose finalizer is held by a lingering pod stays Terminating after
      # the timed delete, and the next deploy's pods then cannot schedule
      # ("persistentvolumeclaim is being deleted"). Clear finalizers on any PVC
      # still present so nothing is left half-deleted.
      for _pvc in $(kubectl -n "$NS" get pvc -o name 2>/dev/null); do
        kubectl -n "$NS" patch "$_pvc" --type=merge \
          -p '{"metadata":{"finalizers":null}}' >/dev/null 2>&1 || true
      done
    else
      echo "▸ deleting k3d cluster ${CLUSTER} (all state)"
      k3d cluster delete "$CLUSTER"
      # The external source replicas are compose stacks outside the cluster.
      # Default: stop them but keep their volumes (restore/seed is idempotent
      # and the next rebuild skips the slow DHIS2 restore).
      # --wipe-sources: `compose down -v`, a factory reset that proves the
      # bundle initialises on a fresh machine (every source re-seeds from the
      # bundle's own seed data).
      if [ "${1:-}" = "--wipe-sources" ]; then
        echo "▸ stopping external source replicas + WIPING their volumes (factory reset)"
        bash "$SCRIPTS/sources-up.sh" down --wipe
      else
        echo "▸ stopping external source replicas (volumes kept: pass --wipe-sources for a factory reset)"
        bash "$SCRIPTS/sources-up.sh" down
      fi
    fi
    exit 0 ;;
esac
FAST_BOOT=0
for a in "$@"; do case "$a" in
  --rebuild-images) REBUILD_IMAGES=1 ;;
  --offline) OFFLINE=1 ;;
  # --fast: restore the optional derived snapshot (bundle/state/derived) instead
  # of re-deriving marts through the pipeline. It is a cache for quick
  # spin-ups and falls back to the full pipeline if no snapshot is packed. The
  # default boot always re-derives from source, which also serves as the
  # integration test. Produce the snapshot with pack.sh --with-derived.
  --fast) FAST_BOOT=1; export NHDW_FAST_BOOT=1 ;;
  -h|--help) sed -n '1,30p' "$0"; exit 0 ;;
  *) echo "unknown arg: $a" >&2; exit 2 ;;
esac; done
rm -f /tmp/nhdw-fast-boot.flag   # restore-state writes it iff the snapshot restores

step() { echo; echo "── $* ──"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "FATAL: missing $1" >&2; exit 1; }; }

# Repair Cilium node-IP drift (idempotent; a no-op on a healthy cluster).
# k3d on Docker Desktop can reassign the node container IPs across a daemon or
# host restart. Cilium caches each node's IP in its CiliumNode CR and keys its
# cross-node VXLAN tunnel on it, so a reshuffle silently drops all cross-node
# pod traffic (Keycloak to postgres-vault, gateway to DB, ML jobs) while the
# cluster still reports up. Real nodes have stable IPs, so this cannot occur in
# production and cannot be prevented here. The bring-up therefore detects the
# drift (k8s InternalIP != CiliumNode IP) and reconciles it in place; stuck pods
# recover once cross-node networking is restored.
heal_cilium_node_ip_drift() {
  kubectl get crd ciliumnodes.cilium.io >/dev/null 2>&1 || return 0   # Cilium not installed (yet)
  local n k8s cil drift=0
  for n in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
    k8s=$(kubectl get node "$n" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
    cil=$(kubectl get ciliumnode "$n" -o jsonpath='{.spec.addresses[?(@.type=="InternalIP")].ip}' 2>/dev/null)
    if [ -n "$k8s" ] && [ "$k8s" != "$cil" ]; then
      echo "  ! Cilium node-IP drift on $n (k8s=$k8s cilium=${cil:-none})"; drift=1
    fi
  done
  if [ "$drift" = 1 ]; then
    echo "  ▸ reconciling Cilium to the current node IPs (drop stale CiliumNode CRs + restart agents)"
    kubectl delete ciliumnode --all >/dev/null 2>&1 || true
    kubectl -n kube-system delete pod -l k8s-app=cilium >/dev/null 2>&1 || true
    kubectl -n kube-system rollout status ds/cilium --timeout=180s >/dev/null 2>&1 || true
    echo "  ok: Cilium reconciled: cross-node networking restored (dependent pods self-heal)"
  else
    echo "  ok: Cilium node IPs consistent: no cross-node drift"
  fi
}

# 1. tools
step "1 · tools"
need docker; need kubectl; need python3
docker info >/dev/null 2>&1 || { echo "FATAL: docker daemon not running"; exit 2; }
if ! command -v k3d >/dev/null; then
  echo "  installing k3d -> ~/.local/bin"; mkdir -p ~/.local/bin
  curl -sfL https://github.com/k3d-io/k3d/releases/download/v5.7.5/k3d-linux-amd64 -o ~/.local/bin/k3d
  chmod +x ~/.local/bin/k3d
fi
if ! command -v kustomize >/dev/null; then
  echo "  installing kustomize -> ~/.local/bin"
  curl -sfL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz | tar -xz -C ~/.local/bin kustomize
fi

# 2. cluster (host 80/443 -> Traefik, so the *.127.0.0.1.nip.io ingress works)
step "2 · k3d cluster ${CLUSTER}"
if k3d cluster list 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER"; then
  echo "  cluster exists: reusing"
  # A reused cluster may be recovering from a host or Docker restart that
  # reshuffled the node IPs; reconcile Cilium before relying on cross-node
  # pod traffic (see heal_cilium_node_ip_drift).
  heal_cilium_node_ip_drift
else
  # Pass the host GPU into the cluster when one is present. The assistant LLM
  # runs outside the cluster (0_external/llm/) and gets its GPU from its own
  # container (sources-up.sh layers docker-compose.gpu.yml). This passthrough
  # serves only the bare-metal Linux in-cluster GPU recipe
  # (0_external/llm/baremetal/, which also needs the custom nhdw/k3s-cuda node
  # image). On WSL2 in-cluster GPU is unavailable and the flag is a no-op.
  # CPU-only machines skip it.
  GPU_FLAG=""
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo "  ▸ host GPU detected: passing --gpus all to k3d (external ollama uses the GPU directly)"
    GPU_FLAG="--gpus all"
  fi
  # Flannel is disabled: Cilium is the CNI, and Hubble gives the topology page
  # per-pod flow metrics, including controller-less lab pods.
  # k3d --wait is satisfied by the k3s process (the node stays NotReady until
  # the Cilium install below; coredns and traefik stay Pending briefly, then
  # recover). The k3s version is pinned and the bundled node image loaded so
  # cluster creation works offline.
  K3S_IMAGE="${K3S_IMAGE:-rancher/k3s:v1.35.5-k3s1}"
  for t in "$REPO"/bundle/images/system/node-*.tar; do
    [ -f "$t" ] && docker load -i "$t" >/dev/null 2>&1 || true
  done
  k3d cluster create "$CLUSTER" --image "$K3S_IMAGE" --servers 1 --agents 1 $GPU_FLAG \
    --port "80:80@loadbalancer" --port "443:443@loadbalancer" \
    --k3s-arg "--disable=metrics-server@server:0" \
    --k3s-arg "--flannel-backend=none@server:0" \
    --k3s-arg "--disable-network-policy@server:0" --wait

  # Import the bundled k3s system images (pause, coredns, traefik, klipper,
  # local-path, busybox) before anything schedules, so an offline boot never
  # pulls them; a fresh k3d node embeds none. Import from the tars (sanitised by
  # fetch-system-images.sh): importing by image name fails inside the node's ctr.
  SYS_TARS=()
  for t in "$REPO"/bundle/images/system/*.tar; do
    case "$t" in */node-*) continue ;; esac
    [ -f "$t" ] && SYS_TARS+=("$t")
  done
  if [ ${#SYS_TARS[@]} -gt 0 ]; then
    k3d image import "${SYS_TARS[@]}" -c "$CLUSTER" --mode direct >/dev/null 2>&1 \
      && echo "  ok: imported ${#SYS_TARS[@]} bundled k3s system images (offline-ready)" \
      || echo "  ! system-image import had errors: offline boot may pull pause/coredns"
  else
    echo "  ! no bundled system-image tars: run bundle/scripts/fetch-system-images.sh for offline boot"
  fi

  # Cilium CNI + Hubble, from the bundled chart and image tars
  echo "  ▸ installing Cilium CNI + Hubble (bundled chart)"
  # bpf and cgroup2 mounts the agent needs inside the k3d node containers
  for n in $(docker ps --format '{{.Names}}' | grep "k3d-$CLUSTER-" | grep -v serverlb); do
    docker exec "$n" sh -c \
      "mount bpffs -t bpf /sys/fs/bpf 2>/dev/null; mount --make-shared /sys/fs/bpf 2>/dev/null; \
       mkdir -p /run/cilium/cgroupv2; mount -t cgroup2 none /run/cilium/cgroupv2 2>/dev/null; \
       mount --make-shared /run/cilium/cgroupv2 2>/dev/null" || true
  done
  # Pre-import the Cilium images so an offline boot never pulls them.
  for t in "$REPO"/bundle/images/cilium_*.tar; do
    [ -f "$t" ] || continue
    docker load -i "$t" >/dev/null 2>&1 || true
  done
  CIL_IMGS=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(quay.io/)?cilium/' | head -6)
  [ -n "$CIL_IMGS" ] && k3d image import $CIL_IMGS -c "$CLUSTER" >/dev/null 2>&1 || true
  HELM="$REPO/bundle/bin/helm"
  if [ ! -x "$HELM" ]; then
    # bundle/bin binaries ship in the release archive, not in git (as does mc).
    # A bare checkout falls back to a download and warns that the bundle is
    # incomplete.
    echo "  ! bundle/bin/helm missing: downloading (FIX THE BUNDLE)" >&2
    mkdir -p "$REPO/bundle/bin"
    curl -fsSL --max-time 180 https://get.helm.sh/helm-v3.16.4-linux-amd64.tar.gz \
      | tar -xzO linux-amd64/helm > "$HELM" && chmod +x "$HELM" || true
  fi
  CHART=$(ls "$REPO"/bundle/charts/cilium-*.tgz 2>/dev/null | head -1)
  if [ -x "$HELM" ] && [ -n "$CHART" ]; then
    # useDigest=false everywhere: the chart pins images by @sha256 by default,
    # which bypasses the tag-imported bundle tars and forces slow network pulls.
    # Envoy stays embedded (no separate DaemonSet), one fewer image to bundle.
    "$HELM" upgrade --install cilium "$CHART" -n kube-system \
      --set operator.replicas=1 \
      --set ipam.mode=kubernetes \
      --set image.pullPolicy=IfNotPresent --set image.useDigest=false \
      --set operator.image.pullPolicy=IfNotPresent --set operator.image.useDigest=false \
      --set envoy.enabled=false \
      --set hubble.enabled=true \
      --set hubble.relay.enabled=true \
      --set hubble.relay.image.pullPolicy=IfNotPresent --set hubble.relay.image.useDigest=false \
      --set prometheus.enabled=true \
      --set "hubble.metrics.enabled={flow:sourceContext=workload-name|pod-name;destinationContext=workload-name|pod-name;labelsContext=source_namespace\,destination_namespace,tcp:sourceContext=workload-name|pod-name;destinationContext=workload-name|pod-name;labelsContext=source_namespace\,destination_namespace,drop:sourceContext=workload-name|pod-name;destinationContext=workload-name|pod-name;labelsContext=source_namespace\,destination_namespace,dns}" \
      >/dev/null
    echo "  ▸ waiting for the Cilium agent to be ready (CNI comes up now)"
    # 600s: on a cold boot (fresh cluster, images just loaded, BPF/cgroup2
    # mounts, WSL2) the agent DaemonSet needs about 5 to 6 minutes to roll out,
    # so a 300s window aborts boots whose Cilium is healthy. Warm boots finish fast.
    kubectl -n kube-system rollout status ds/cilium --timeout=600s \
      && echo "  ok: Cilium CNI ready (Hubble metrics on :9965)" \
      || { echo "  FAIL: Cilium failed to come up: aborting boot (revert: tag coroot-baseline-2026-07-10)"; exit 1; }
  else
    echo "  FAIL: bundled helm/chart missing (bundle/bin/helm + bundle/charts/cilium-*.tgz): cannot install CNI"; exit 1
  fi
  # NVIDIA k8s device plugin: advertises nvidia.com/gpu for the bare-metal
  # in-cluster recipe only (a stock k3s node cannot enumerate GPUs without the
  # custom CUDA node image, and WSL2 cannot at all). Best-effort; failure never
  # blocks the boot. The default LLM path (external ollama) does not use it.
  if [ -n "$GPU_FLAG" ]; then
    kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml \
      2>/dev/null && echo "  ok: NVIDIA device plugin applied (bare-metal in-cluster GPU only)" \
      || echo "  ! device-plugin apply failed/absent: irrelevant to the external LLM"
  fi
fi

# 3. images: load the pre-baked bundle tars into k3d
# The release archive ships every image as a tar (third-party and the built
# nhdw/*), so a cold boot loads and imports them with no registry pulls and no
# build. --rebuild-images is the maintainer path that rebuilds nhdw/* first.
step "3 · images"
if [ "$REBUILD_IMAGES" = 1 ]; then
  echo "  --rebuild-images: building nhdw/* from source, then loading public tars"
  bash "$SCRIPTS/build-images.sh" --import
  bash "$SCRIPTS/cache-images.sh" --load-only 2>/dev/null || true
elif ls "$REPO"/bundle/images/*.tar >/dev/null 2>&1; then
  echo "  loading all pre-baked images from bundle/images (offline)"
  bash "$SCRIPTS/cache-images.sh" --load-only
else
  echo "  no bundle tars found: building nhdw/* + pulling public images (needs internet)"
  bash "$SCRIPTS/build-images.sh" --import
  bash "$SCRIPTS/cache-images.sh" 2>/dev/null || true
fi

# 4. apply the dev overlay (manifests + secrets + ingress)
# Render secrets.yaml from the committed template with per-deploy random values
# if it does not already exist (a fresh checkout has none; a shipped bundle
# carries one). An existing file is never overwritten, so a warm boot keeps the
# passwords the databases were initialised with. Real Google/SMTP credentials
# are injected by exporting GOOGLE_CLIENT_ID, SMTP_USER, etc. before bootstrap.
step "4 · kustomize apply (overlays/dev)"
bash "$SCRIPTS/gen-secrets.sh"
# Locally trusted wildcard TLS (nhdw-wildcard-tls). The CA persists in
# bundle/tls, so it is imported into the browser once.
bash "$SCRIPTS/gen-tls.sh" || echo "  ! gen-tls.sh failed (dev falls back to traefik self-signed)"
kustomize build --load-restrictor=LoadRestrictionsNone "$OVERLAY" | kubectl apply -f -

# Headlamp needs no boot-time auth setup: the headlamp-gate (nhdw/tool-gate)
# mounts the headlamp-token secret and injects the service-account bearer on
# every proxied HTTP/WS request (INJECT_BEARER_FILE). It reads the token file
# per request, so a late-populated secret is picked up. See tool-gates.yaml.

# Point the gateway at the assistant model the external ollama serves: 14b when
# its weights are present, else 3b. sources-up.sh (step 6) brings up that ollama
# with the matching GPU or CPU compose file. Best-effort; never blocks.
bash "$SCRIPTS/gpu-detect.sh" "$NS" 2>/dev/null || true

# metrics-server (kube-system) is applied separately from the overlay, because
# the overlay pins namespace=nhdw and would move it out of kube-system. It backs
# `kubectl top` and the gateway HPA. The k3s bundled copy is disabled at cluster
# creation; this is the explicit, offline-cached one.
kubectl apply -f "$REPO/1_infrastructure/k8s/base/metrics-server.yaml"

step "5 · wait for postgres-vault + keycloak-seed"
for i in $(seq 1 30); do
  [ "$(kubectl -n "$NS" get pod postgres-vault-0 -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || true)" = "true" ] && break
  sleep 5
done
# Ready is not initialised. A failed init script aborts the initdb sequence;
# the container then restarts onto the existing volume, skips the remaining
# scripts forever, and answers probes while missing half its schemas. The
# sentinel (99_init_complete.sql, sorts last) exists only if every script ran.
for i in $(seq 1 24); do
  SENTINEL=$(kubectl -n "$NS" exec postgres-vault-0 -- psql -U vault_admin -d nhdw_vault -tAc \
    "SELECT count(*) FROM public.initdb_sentinel" 2>/dev/null || echo 0)
  [ "${SENTINEL:-0}" = "1" ] && break
  sleep 5
done
if [ "${SENTINEL:-0}" != "1" ]; then
  echo "  FAIL: postgres answers but the initdb sentinel is missing, an init script"
  echo "    failed and the remaining ones never ran. Read:"
  echo "      kubectl -n $NS logs postgres-vault-0 --previous | grep -B2 -iE 'fatal|error'"
  echo "    Fix the cause (a missing secret, usually), then wipe the volume so"
  echo "    init re-runs:  kubectl -n $NS delete pod postgres-vault-0 && kubectl -n $NS delete pvc -l app=postgres-vault"
  exit 1
fi
echo "  postgres-vault ready AND fully initialized (sentinel present)"
kubectl -n "$NS" wait --for=condition=complete job/keycloak-seed --timeout=420s 2>/dev/null \
  && echo "  keycloak-seed complete" || echo "  ! keycloak-seed not complete (inspect: kubectl -n $NS logs job/keycloak-seed)"


# 5.2 · reconcile the Superset metadata DB and role. 31_superset_db.sh
# provisions them during postgres-vault's one-time initdb, which needs
# SUPERSET_DB_PASSWORD from the superset-secrets Secret. That env ref is optional
# and races the secret (both land in the same kustomize apply), so on a cold boot
# the role can be silently skipped and Superset cannot authenticate. Re-run the
# idempotent provisioning now that postgres-vault is up and the secret exists,
# then restart Superset to pick up the role (non-blocking; nothing gates on it).
step "5.2 · reconcile superset metadata DB/role (initdb secret-race guard)"
SS_PW=$(kubectl -n "$NS" get secret superset-secrets -o jsonpath='{.data.DB_PASSWORD}' 2>/dev/null | base64 -d)
PGU=$(kubectl -n "$NS" exec postgres-vault-0 -- printenv POSTGRES_USER 2>/dev/null)
if [ -n "$SS_PW" ] && [ -n "$PGU" ]; then
  if cat "$REPO/3_storage/postgres/init/31_superset_db.sh" \
       | kubectl -n "$NS" exec -i postgres-vault-0 -- \
           env SUPERSET_DB_PASSWORD="$SS_PW" POSTGRES_USER="$PGU" bash -s >/dev/null 2>&1; then
    echo "  ok: superset_writer + nhdw_superset reconciled"
    kubectl -n "$NS" rollout restart deploy/superset >/dev/null 2>&1 || true
  else
    echo "  ! superset metadata reconcile failed: Superset may not start" >&2
  fi
else
  echo "  ! superset-secrets.DB_PASSWORD or postgres user unavailable; skipping" >&2
fi

# 5.5 packed runtime state (optional; factory bundles have none)
step "5.5 · packed state (bundle/state: restored before seeds, if present)"
bash "$SCRIPTS/restore-state.sh"

# The reference vocabulary must exist before the dbt-vault build: the business
# vault resolution views join nhdw_ref.source_to_concept_map at build time
# (see 3_storage/postgres/init/34_ref_vocab.sql).
step "5.7 · reference vocabulary (nhdw_ref crosswalks + concepts)"
bash "$SCRIPTS/seed-ref-vocab.sh" \
  || { echo "ERROR: nhdw_ref seed FAILED: the vault build would mis-resolve; aborting."; exit 1; }

# 6. seed the data tiers (sources -> ingestors -> dbt pipeline)
step "6 · DATA seed (sources -> ingestors -> dbt pipeline)"

# The in-cluster ingestors reach the host-published source ports via
# host.k3d.internal, an entry k3d injects into CoreDNS at cluster creation.
# This only verifies it (orchestration does not patch system config); if it is
# missing the cluster is malformed and must be recreated.
if kubectl -n kube-system get cm coredns -o jsonpath='{.data.NodeHosts}' 2>/dev/null \
    | grep -q 'host\.k3d\.internal'; then
  echo "  host.k3d.internal present in CoreDNS NodeHosts ok"
else
  echo "  FAIL: host.k3d.internal missing from CoreDNS: the k3d cluster is malformed."
  echo "    Recreate it: bootstrap.sh teardown && bootstrap.sh   (k3d injects the entry at creation)"
  exit 1
fi

# Bring up and restore/seed the external source replicas the ingestors pull
# from. Sources come up in both modes: the app and access flows need the live
# replicas, and the fast-boot cache needs a source to reconcile against.
bash "$SCRIPTS/sources-up.sh" up

# A failed data seed fails the boot.
# Fast boot: if restore-state restored the derived snapshot, skip the pipeline
# (the marts are already in place); with no snapshot, run the full pipeline.
# The default (no --fast) always runs the full pipeline.
if [ "$FAST_BOOT" = 1 ] && [ -f /tmp/nhdw-fast-boot.flag ]; then
  echo "  FAST BOOT: marts restored from the derived snapshot; SKIPPING the"
  echo "     ingestion + dbt pipeline. Run WITHOUT --fast to re-derive from source"
  echo "     (the source of truth; a full rebuild reconciles the cache)."
else
  [ "$FAST_BOOT" = 1 ] && echo "  (--fast requested but no derived snapshot packed, running the full pipeline)"
  bash "$SCRIPTS/seed-realistic.sh" || { echo; echo "ERROR: DATA seed FAILED: the boot is not demoable. See the errors above."; exit 1; }
fi

# 7. seed the app/security state (not pipeline-derivable)
# A boot without the RBAC and user model is unusable, so a failed app/security
# seed fails the boot.
step "7 · APP/SECURITY seed (RBAC + workshop groups: from files)"
bash "$SCRIPTS/seed-app-security.sh" || { echo; echo "ERROR: APP/SECURITY seed FAILED: the boot is not demoable. See the errors above."; exit 1; }

# 7.1 · regenerate the OPA policy from the permission store. Step 7
# (re)populated ops_perm.permission and role_permission; regenerate the rego
# from the store (compile.py --from-db), gate it with `opa test`, and reload
# OPA, so the database is the source of the deployed policy (permission-admin
# edits take effect on the next boot). Fail-closed: a failing opa test leaves
# OPA untouched and fails the boot. Runs before the gateway restart (7.2),
# whose self-test evaluates against OPA.
step "7.1 · regen OPA policy from the permission store (opa test gated)"
bash "$SCRIPTS/regen-opa-policy.sh" \
  || { echo; echo "ERROR: OPA policy regen FAILED: see errors above."; exit 1; }

# 7.2 · gateway restart. The gateway runs an OPA self-test at startup that
# needs the mart_attrs catalog seeded in step 7. It is deployed with the
# manifests (step 4), so on a cold boot it starts against an empty catalog,
# fails closed and enters CrashLoopBackOff. Restart it now that the catalog
# exists, and gate the gateway-driven seeds below (7.55, 7.57, 7.6) on it being
# Ready so they do not race a recovering gateway and time out.
step "7.2 · restart gateway (its OPA self-test needs the step-7 catalog)"
kubectl -n "$NS" rollout restart deploy/nhdw-gateway >/dev/null 2>&1 || true
kubectl -n "$NS" rollout status deploy/nhdw-gateway --timeout=180s \
  || { echo; echo "ERROR: gateway not Ready after the security seed (OPA self-test likely still failing: see 'kubectl -n $NS logs deploy/nhdw-gateway')."; exit 1; }

# 7.05 · docs seed (docs/*.md -> object store nhdw-docs, served via /gw/docs)
# Publishes the curated operator docs into the object store (SeaweedFS) so the
# admin Documentation page can open and download them; idempotent and persistent
# across reboots. The platform works without them, but the boot gates on this
# step so a missing doc is reported at once.
step "7.05 · docs seed (docs/*.md -> object store nhdw-docs)"
bash "$SCRIPTS/seed-docs.sh" || { echo; echo "ERROR: docs seed FAILED: the Documentation page will be empty. See the errors above."; exit 1; }

# 7.5 · ML seed (packages -> registry -> first predictions)
# Convergent like every other seed: models already @champion (restored state,
# warm boots) are skipped, so this is cheap on warm boots and complete on cold
# ones. Forecast widgets have data before the first scheduled run.
step "7.5 · ML seed (bundle/seed-pack/app/ml -> MLflow registry -> ops_ml.ml_predictions)"
bash "$SCRIPTS/seed-ml.sh" || { echo; echo "ERROR: ML seed FAILED: forecast widgets will be empty. See the errors above."; exit 1; }

# 7.55 · projects + datasets seed (/api/projects + /api/datasets)
# Every dataset and model belongs to a project. Creates the sample projects and
# generated datasets through the gateway APIs (the dataset generator runs the
# governed SQL chain against the freshly built marts). Runs before the
# graduation seed, whose submissions bind to these projects. Convergent; a
# failure leaves the workshop without content.
step "7.55 · projects + datasets seed (real /api/projects + /api/datasets APIs)"
bash "$SCRIPTS/seed-projects.sh" || { echo; echo "ERROR: projects/datasets seed FAILED: the workshop is not demoable. See the errors above."; exit 1; }

# 7.57 · resource-request seed (request + admin-decide gateway API)
# Leaves the compute review queue with a traceable pending and an approved
# request, using the same /api/workspace/resource-request and .../decide
# endpoints a workshop user and admin use (no direct row inserts). Convergent;
# a failure leaves the admin compute-review queue empty, so the boot gates on it.
step "7.57 · resource-request seed (real /api/workspace/resource-request + decide APIs)"
bash "$SCRIPTS/seed-resource-requests.sh" || { echo; echo "ERROR: resource-request seed FAILED: the compute review queue is not demoable. See the errors above."; exit 1; }

# 7.58 · model-projects seed (platform models -> approved public projects)
# Makes each platform ML model (trained and integrated by the ML seed, 7.5) an
# approved workshop project: a project row, its package files, a kind='model'
# artifact bound to the existing MLflow model, and an approved review record.
# Uses POST /api/graduation/.../adopt-model (no direct row inserts; it reuses
# the integrated model and does not retrain). Runs after the ML (7.5) and
# projects (7.55) seeds and before the community graduation seed (7.6).
# Convergent; a failure leaves the platform models without a project, so the
# boot gates on it.
step "7.58 · model-projects seed (bundle/seed-pack/app/ml -> real adopt-model API)"
bash "$SCRIPTS/seed-model-projects.sh" || { echo; echo "ERROR: model-projects seed FAILED: the platform models have no project home. See the errors above."; exit 1; }

# 7.6 · graduation seed (project review flow via the gateway API)
# Populates the project-review lifecycle through the same submit, approve and
# reject endpoints a contributor and reviewer use (no direct inserts into
# project or project_review). Leaves three example community projects: one
# approved and public with its model integrated into the catalogue by the
# graduation hook, one in_review feeding the reviewer queue, and one
# changes_requested. Runs after the projects (7.55) and ML (7.5) seeds and needs
# the gateway and the seeded users (step 7; owner and reviewer reference
# ops_identity.user). Convergent (keys off each project's current
# review_status); a failure leaves the review flow and community-model
# catalogue empty, so the boot gates on it.
step "7.6 · graduation seed (bundle/seed-pack/app/project-review -> real submit/approve/reject API)"
bash "$SCRIPTS/seed-graduation.sh" || { echo; echo "ERROR: graduation seed FAILED: the review flow / model catalogue is not demoable. See the errors above."; exit 1; }

# 7.8 · OpenMetadata catalog seed (metadata plane)
# Waits for the OpenMetadata API, validates the ingestion-bot token and triggers
# the connectors DAG once. Non-fatal: nothing depends on the catalog
# (docs/metadata-plane.md), so a boot without OpenMetadata, or before its
# manual bot-token step, is fully usable; this step warns and continues.
step "7.8 · OpenMetadata catalog seed (non-fatal)"
bash "$SCRIPTS/seed-openmetadata.sh" \
  || echo "  ! OpenMetadata seed incomplete (catalog-only: the platform is unaffected); see above."

# 7.85 · DQ detection seed (governance plane)
# Runs the incident detector once so the Admin DQ queue and governance KPIs
# have content on a cold boot. The incidents are findings over the just-built
# pipeline (stale sources, failed dbt tests, MDM backlog), not fixtures.
# Non-fatal: the queue is derived state, and the 06:15 DAG catches up.
step "7.85 · DQ detection seed (non-fatal)"
DQTOK=$(kubectl -n "$NS" get secret dq-detect-secrets -o jsonpath='{.data.TOKEN}' 2>/dev/null | base64 -d || true)
if [ -n "$DQTOK" ]; then
  kubectl -n "$NS" run dq-detect-seed --rm -i --restart=Never --quiet \
    --image=curlimages/curl:8.10.1 -- -s -m 120 -X POST \
    -H "X-Detect-Token: $DQTOK" \
    "http://nhdw-gateway.$NS.svc.cluster.local:8000/api/dq/detect" \
    && echo "  ok: first detection ran" \
    || echo "  ! DQ detection seed incomplete (queue-only: the platform is unaffected)"
else
  echo "  ! dq-detect-secrets absent: skipping (the 06:15 DAG will populate the queue)"
fi

# 7.9 · verification gate
# Every prior step exiting 0 does not prove the platform is usable (a mart can
# be empty, a contract unmet), so the fast regression check runs as a hard
# gate. On a clean boot it passes in seconds; on a bad one it prints the failing
# contracts here. SKIP_VERIFY=1 bypasses it for a partial boot.
if [ "${SKIP_VERIFY:-0}" != "1" ]; then
  step "7.9 · verify-system (fast regression gate)"
  if ! bash "$SCRIPTS/verify-system.sh"; then
    echo
    echo "ERROR: verify-system found problems: the boot ran to completion but one or"
    echo "  more contracts are not satisfied (see the FAIL: lines above). The demo may"
    echo "  be partially broken; investigate before relying on it. Re-run"
    echo "  '$SCRIPTS/verify-system.sh' after any fix. (SKIP_VERIFY=1 to bypass.)"
    exit 1
  fi
fi

# 8. done
step "8 · DONE: NHDW demo is up"
cat <<EOF

  Dashboards (HTTPS, self-signed: accept the cert):
    Public        https://public.127.0.0.1.nip.io/
    Internal BI   https://internal-bi.127.0.0.1.nip.io/
    Admin         https://admin.127.0.0.1.nip.io/
    Workshop      https://workshop.127.0.0.1.nip.io/
    Keycloak      https://keycloak.127.0.0.1.nip.io/
    Gateway       https://gateway.127.0.0.1.nip.io/health
    MLflow        https://mlflow.127.0.0.1.nip.io/

  Sign in: tadesse.h with the SEED_PASSWORD from demo-user-secrets (R-DEC on moh-staff)
  Teardown:  bootstrap.sh teardown
EOF
