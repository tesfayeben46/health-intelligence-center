"""/api/topology: data for the admin dashboard's topology widget.

Each layer is fetched separately so the widget can switch views without
re-querying everything:

  GET  /api/topology/infra          host summary from /proc (CPU, memory, load)
  GET  /api/topology/orchestration  pods and services from the Kubernetes API,
                                    or a docker container view when no cluster
                                    API is configured
  GET  /api/topology/graph          application topology: nodes are deployed
                                    Deployments and StatefulSets; edges are the
                                    wiring derived from each workload's env,
                                    args and ConfigMap connection strings,
                                    reconciled against the live scan (declared
                                    and observed, health, phantom), with per-edge
                                    traffic from the traffic providers (see
                                    app/topology_graph.py, app/traffic_providers.py)
  GET  /api/topology/traffic        windowed per-edge RED metrics (request rate,
                                    error rate, p95, bytes) from
                                    ops_audit.gateway_log; also feeds /graph
  GET  /api/topology/node           side-panel detail for one node
  GET  /api/topology/errors         recent error log lines for a node (Loki)
  GET  /api/topology/logs           tail of a node's container log (kubelet)
  GET  /api/topology/cluster        pod phases, node usage, HPA status
  POST /api/topology/scale          set replicas on a Deployment
  GET  /api/topology/dataflow       deprecated stub returning an empty graph;
                                    pipeline lineage is in dbt Docs

Each response is a snapshot; the widget controls the polling interval.

Restricted to R-OPS-* roles and R-SUPER.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import UserCtx, get_user
from .. import ops_db
from .. import topology_graph
from .. import traffic_providers
# Authorisation is _require_ops (the caller's roles must intersect OPS_ROLES).
# There is no opa.decide call: no Rego rule matches a topology action, so under
# OPA_ENFORCE it would deny every ops read.

log = logging.getLogger("gateway.topology")
router = APIRouter(prefix="/api/topology", tags=["topology"])

DOCKER_SOCK = "unix:///var/run/docker.sock"

# Inside a k8s pod the API server lives at https://kubernetes.default.svc,
# the SA token at /var/run/secrets/.../token, the CA at .../ca.crt.
# Env overrides supported for testing outside the cluster.
_SA_DIR     = "/var/run/secrets/kubernetes.io/serviceaccount"
_SA_TOKEN_F = f"{_SA_DIR}/token"
_SA_CA_F    = f"{_SA_DIR}/ca.crt"
_SA_NS_F    = f"{_SA_DIR}/namespace"


def _kube_api() -> str:
    return os.environ.get("KUBE_API") or (
        "https://kubernetes.default.svc" if os.path.exists(_SA_TOKEN_F) else "")


def _kube_token() -> str:
    env = os.environ.get("KUBE_TOKEN", "")
    if env:
        return env
    try:
        with open(_SA_TOKEN_F) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _kube_ns() -> str:
    env = os.environ.get("KUBE_NAMESPACE", "")
    if env:
        return env
    try:
        with open(_SA_NS_F) as f:
            return f.read().strip() or "nhdw"
    except FileNotFoundError:
        return "nhdw"


def _kube_ca() -> str | bool:
    return _SA_CA_F if os.path.exists(_SA_CA_F) else False


# Module-level values used by /orchestration, resolved once at import.
KUBE_API   = _kube_api()
KUBE_TOKEN = _kube_token()

OPS_ROLES = {"R-OPS-ADM", "R-OPS-ENG", "R-OPS-SEC", "R-SUPER"}


def _require_ops(user: UserCtx) -> None:
    if not (set(user.roles) & OPS_ROLES):
        raise HTTPException(403, "/api/topology is for R-OPS-* roles only")


# Kubernetes quantity parsers (for /node resource usage)
# metrics.k8s.io reports CPU in nanocores ("1234567n") and memory with binary
# suffixes ("128Mi"); pod-spec requests and limits use "100m" or "1" for CPU and
# "128Mi" or "1Gi" for memory. CPU is normalised to millicores, memory to bytes.
_MEM_MULT = {
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "Pi": 1024 ** 5,
    "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4, "P": 1000 ** 5,
    "k": 1000,
}


def _parse_cpu_millicores(v: Any) -> int | None:
    """'100m' -> 100, '1' or '1.0' -> 1000, '1234567n' (nanocores) -> 1,
    '500u' (microcores) -> 0 (rounded)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        if s.endswith("n"):
            return int(round(float(s[:-1]) / 1_000_000))
        if s.endswith("u"):
            return int(round(float(s[:-1]) / 1_000))
        if s.endswith("m"):
            return int(round(float(s[:-1])))
        return int(round(float(s) * 1000))
    except ValueError:
        return None


def _parse_mem_bytes(v: Any) -> int | None:
    """'128Mi' -> 134217728, '1Gi' -> 1073741824, '134217728' -> 134217728."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for suf, mult in _MEM_MULT.items():
        if s.endswith(suf):
            try:
                return int(round(float(s[:-len(suf)]) * mult))
            except ValueError:
                return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _labels_match(pod_labels: dict[str, str], selector: dict[str, str]) -> bool:
    return bool(selector) and all(pod_labels.get(k) == v for k, v in selector.items())


def _extract_mounted_config(pod_spec: dict[str, Any],
                            containers: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Names of every Secret and ConfigMap the workload mounts or reads: the pod
    template's volumes[].secret, .configMap and .projected.sources, and each
    container's envFrom[].secretRef/.configMapRef and
    env[].valueFrom.secretKeyRef/.configMapKeyRef. Shown in the node detail
    panel; they are not drawn as graph nodes. Only names are returned."""
    secrets: set[str] = set()
    cms: set[str] = set()
    for v in pod_spec.get("volumes") or []:
        s = (v.get("secret") or {}).get("secretName")
        if s:
            secrets.add(s)
        cm = (v.get("configMap") or {}).get("name")
        if cm:
            cms.add(cm)
        for src in (v.get("projected") or {}).get("sources") or []:
            ps = (src.get("secret") or {}).get("name")
            if ps:
                secrets.add(ps)
            pc = (src.get("configMap") or {}).get("name")
            if pc:
                cms.add(pc)
    for ct in containers:
        for ef in ct.get("envFrom") or []:
            s = (ef.get("secretRef") or {}).get("name")
            if s:
                secrets.add(s)
            cm = (ef.get("configMapRef") or {}).get("name")
            if cm:
                cms.add(cm)
        for e in ct.get("env") or []:
            vf = e.get("valueFrom") or {}
            s = (vf.get("secretKeyRef") or {}).get("name")
            if s:
                secrets.add(s)
            cm = (vf.get("configMapKeyRef") or {}).get("name")
            if cm:
                cms.add(cm)
    return {"secrets": sorted(secrets), "configMaps": sorted(cms)}


async def _pod_storage(c: httpx.AsyncClient, api: str, ns: str,
                       pod_names: set[str], pod_nodes: set[str]) -> dict[str, Any]:
    """Ephemeral-storage and per-PVC volume usage for a workload's pods, read
    from each hosting node's kubelet Summary API
    (``GET /api/v1/nodes/{node}/proxy/stats/summary``, which needs nodes/proxy
    RBAC). Best effort: returns haveStats=False when RBAC or reachability fails,
    so the panel shows stats as unavailable instead of an error."""
    out: dict[str, Any] = {
        "source": "kubelet /stats/summary", "haveStats": False,
        "ephemeral": None, "volumes": [],
    }
    eph_used = 0
    eph_cap = 0
    eph_seen = False
    vols: dict[str, dict[str, Any]] = {}
    for node in sorted(n for n in pod_nodes if n):
        try:
            r = await c.get(f"{api}/api/v1/nodes/{node}/proxy/stats/summary")
        except httpx.HTTPError as exc:
            log.debug("kubelet stats for node %s failed: %s", node, exc)
            continue
        if r.status_code != 200:
            log.debug("kubelet stats for node %s -> HTTP %s", node, r.status_code)
            continue
        for pod in r.json().get("pods") or []:
            ref = pod.get("podRef") or {}
            if ref.get("namespace") != ns or ref.get("name") not in pod_names:
                continue
            out["haveStats"] = True
            es = pod.get("ephemeral-storage") or {}
            if es.get("usedBytes") is not None:
                eph_used += int(es.get("usedBytes") or 0)
                eph_cap = max(eph_cap, int(es.get("capacityBytes") or 0))
                eph_seen = True
            for vol in pod.get("volume") or []:
                pvc = (vol.get("pvcRef") or {}).get("name")
                if not pvc:
                    continue          # config, secret and token tmpfs mounts are not storage
                vols[pvc] = {
                    "name": vol.get("name") or pvc, "pvc": pvc,
                    "usedBytes": int(vol.get("usedBytes") or 0),
                    "capacityBytes": int(vol.get("capacityBytes") or 0),
                }
    if eph_seen:
        out["ephemeral"] = {"usedBytes": eph_used, "capacityBytes": eph_cap}
    out["volumes"] = sorted(vols.values(), key=lambda v: -v["capacityBytes"])
    return out


async def _first_pod_for_workload(c: httpx.AsyncClient, api: str, ns: str,
                                  name: str) -> tuple[str | None, list[str]]:
    """(podName, [containerNames]) for the Deployment or StatefulSet ``name``:
    the first Running pod, else the first pod. Returns (None, containers) when
    the workload has no pod and (None, []) when ``name`` is not a workload in
    the namespace."""
    selector: dict[str, str] = {}
    containers: list[str] = []
    for path in ("deployments", "statefulsets"):
        r = await c.get(f"{api}/apis/apps/v1/namespaces/{ns}/{path}/{name}")
        if r.status_code == 200:
            spec = r.json().get("spec") or {}
            selector = (spec.get("selector") or {}).get("matchLabels") or {}
            containers = [ct.get("name") for ct in
                          (((spec.get("template") or {}).get("spec") or {}).get("containers") or [])
                          if ct.get("name")]
            break
    if not selector:
        return None, containers
    pr = await c.get(f"{api}/api/v1/namespaces/{ns}/pods")
    if pr.status_code != 200:
        return None, containers
    matched = [p for p in pr.json().get("items", [])
               if _labels_match(((p.get("metadata") or {}).get("labels")) or {}, selector)]
    if not matched:
        return None, containers
    running = [p for p in matched if (p.get("status") or {}).get("phase") == "Running"]
    chosen = (running or matched)[0]
    return (chosen.get("metadata") or {}).get("name"), containers


async def _workload_detail(name: str, ns: str) -> dict[str, Any] | None:
    """Detail for a topology node that is a Deployment or StatefulSet named
    `name`: ready replicas, container images, QoS class, restart count, live
    CPU and memory from metrics-server, storage, mounted config and recent
    Events. Returns None when no such workload exists in the namespace
    (external or not deployed).

    Per-service RED metrics and time-series history are not computed here;
    `red` is None with an explanatory `redNote`.
    """
    api = _kube_api()
    token = _kube_token()
    if not api or not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8, verify=_kube_ca(), headers=headers) as c:
        wl = None
        wl_kind = None
        for kind, path in (("Deployment", "deployments"), ("StatefulSet", "statefulsets")):
            r = await c.get(f"{api}/apis/apps/v1/namespaces/{ns}/{path}/{name}")
            if r.status_code == 200:
                wl, wl_kind = r.json(), kind
                break
        if wl is None:
            return None

        spec = wl.get("spec") or {}
        status = wl.get("status") or {}
        selector = (spec.get("selector") or {}).get("matchLabels") or {}
        desired = spec.get("replicas")
        desired = 1 if desired is None else int(desired)
        ready = int(status.get("readyReplicas") or 0)

        pod_spec = ((spec.get("template") or {}).get("spec")) or {}
        containers = pod_spec.get("containers") or []
        images = [ct.get("image") for ct in containers if ct.get("image")]
        # Secret and ConfigMap names referenced via env, envFrom and volumes.
        config = _extract_mounted_config(pod_spec, containers)

        # Sum requests and limits across the workload's containers.
        req_cpu = req_mem = lim_cpu = lim_mem = 0
        have_req = have_lim = False
        for ct in containers:
            res = ct.get("resources") or {}
            for key, cpu_acc, mem_acc in (("requests", "req", "req"), ("limits", "lim", "lim")):
                block = res.get(key) or {}
                mc = _parse_cpu_millicores(block.get("cpu"))
                mb = _parse_mem_bytes(block.get("memory"))
                if key == "requests":
                    if mc:
                        req_cpu += mc; have_req = True
                    if mb:
                        req_mem += mb; have_req = True
                else:
                    if mc:
                        lim_cpu += mc; have_lim = True
                    if mb:
                        lim_mem += mb; have_lim = True

        # Live pods for this workload: restarts, QoS class, pod list.
        pr = await c.get(f"{api}/api/v1/namespaces/{ns}/pods")
        pr.raise_for_status()
        pods = pr.json().get("items", [])
        wl_pods = [p for p in pods
                   if _labels_match(((p.get("metadata") or {}).get("labels")) or {}, selector)]
        restarts = 0
        qos = None
        pod_rows: list[dict[str, Any]] = []
        pod_names: set[str] = set()
        for p in wl_pods:
            pmd = p.get("metadata") or {}
            pst = p.get("status") or {}
            pname = pmd.get("name")
            pod_names.add(pname)
            cs_list = pst.get("containerStatuses") or []
            p_restarts = sum(int(cs.get("restartCount") or 0) for cs in cs_list)
            restarts += p_restarts
            qos = qos or pst.get("qosClass")
            pod_rows.append({
                "name": pname,
                "phase": pst.get("phase"),
                "restarts": p_restarts,
                "node": (p.get("spec") or {}).get("nodeName"),
                "ready": all(cs.get("ready") for cs in cs_list) if cs_list else False,
            })

        # Live CPU and memory from metrics-server (metrics.k8s.io), best effort.
        usage_cpu = usage_mem = 0
        have_metrics = False
        try:
            mr = await c.get(f"{api}/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods")
            if mr.status_code == 200:
                for pm in mr.json().get("items", []):
                    if (pm.get("metadata") or {}).get("name") not in pod_names:
                        continue
                    for ct in pm.get("containers") or []:
                        usage = ct.get("usage") or {}
                        cm = _parse_cpu_millicores(usage.get("cpu"))
                        mm = _parse_mem_bytes(usage.get("memory"))
                        if cm:
                            usage_cpu += cm; have_metrics = True
                        if mm:
                            usage_mem += mm; have_metrics = True
        except httpx.HTTPError as exc:
            log.debug("metrics-server read for %s failed: %s", name, exc)

        # Recent Events for this workload, its ReplicaSets or its pods.
        events: list[dict[str, Any]] = []
        try:
            er = await c.get(f"{api}/api/v1/namespaces/{ns}/events")
            er.raise_for_status()
            evs = er.json().get("items", [])
            for e in evs:
                io = e.get("involvedObject") or {}
                oname = io.get("name") or ""
                if oname == name or oname in pod_names or oname.startswith(name + "-"):
                    events.append({
                        "type": e.get("type"),
                        "reason": e.get("reason"),
                        "message": (e.get("message") or "")[:240],
                        "count": e.get("count") or 1,
                        "object": f"{io.get('kind')}/{oname}",
                        "lastTimestamp": e.get("lastTimestamp") or e.get("eventTime"),
                    })
            events.sort(key=lambda x: x.get("lastTimestamp") or "", reverse=True)
            events = events[:8]
        except httpx.HTTPError as exc:
            log.debug("events read for %s failed: %s", name, exc)

        # Ephemeral and PVC disk usage from the kubelet Summary API, best effort.
        pod_nodes = {r.get("node") for r in pod_rows if r.get("node")}
        try:
            storage = await _pod_storage(c, api, ns, pod_names, pod_nodes)
        except httpx.HTTPError as exc:
            log.debug("storage read for %s failed: %s", name, exc)
            storage = {"source": "kubelet /stats/summary", "haveStats": False,
                       "ephemeral": None, "volumes": []}

    return {
        "id": name,
        "kind": "workload",
        "workloadKind": wl_kind,
        "namespace": ns,
        "deployment": name,
        "config": config,
        "storage": storage,
        "ready": f"{ready}/{desired}",
        "replicas": {"ready": ready, "desired": desired},
        "images": images,
        "qosClass": qos,
        "restarts": restarts,
        "pods": pod_rows,
        "resources": {
            "haveMetrics": have_metrics,
            "cpu": {
                "usageMillicores": usage_cpu if have_metrics else None,
                "requestMillicores": req_cpu if have_req else None,
                "limitMillicores": lim_cpu if have_lim else None,
            },
            "memory": {
                "usageBytes": usage_mem if have_metrics else None,
                "requestBytes": req_mem if have_req else None,
                "limitBytes": lim_mem if have_lim else None,
            },
            "source": "metrics.k8s.io" if have_metrics else None,
        },
        "events": events,
        # Per-service RED is not computed here. CPU and memory above come from
        # metrics-server; gateway edge request rates come from
        # ops_audit.gateway_log via /traffic.
        "red": None,
        "redNote": "Per-service request rate (RED) needs the gateway /metrics "
                   "scrape. CPU/memory above are real (metrics-server); "
                   "gateway-edge req/s is real too; see the Traffic card or an "
                   "edge's detail.",
    }


async def _pod_detail(name: str, ns: str) -> dict[str, Any] | None:
    """Detail for a pod with no owning Deployment or StatefulSet, namely a
    spawned JupyterHub single-user lab pod. Returns the same shape as
    _workload_detail so the side panel renders it identically: phase, node,
    restarts, images, and live CPU and memory from metrics-server (namespaced,
    so the gateway service account can read it). Returns None when ``name`` is
    not a single-user lab pod."""
    api = _kube_api()
    token = _kube_token()
    if not api or not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8, verify=_kube_ca(), headers=headers) as c:
        pr = await c.get(f"{api}/api/v1/namespaces/{ns}/pods/{name}")
        if pr.status_code != 200:
            return None
        pod = pr.json()
        md = pod.get("metadata") or {}
        labels = md.get("labels") or {}
        if not (labels.get("app") == "jupyterhub"
                and labels.get("component") == "singleuser-server"):
            return None            # not a spawned lab pod; the caller answers
        st = pod.get("status") or {}
        spec = pod.get("spec") or {}
        containers = spec.get("containers") or []
        images = [ct.get("image") for ct in containers if ct.get("image")]
        cs_list = st.get("containerStatuses") or []
        restarts = sum(int(cs.get("restartCount") or 0) for cs in cs_list)
        ready = bool(cs_list) and all(cs.get("ready") for cs in cs_list)

        req_cpu = req_mem = lim_cpu = lim_mem = 0
        have_req = have_lim = False
        for ct in containers:
            res = ct.get("resources") or {}
            rc = _parse_cpu_millicores((res.get("requests") or {}).get("cpu"))
            rm = _parse_mem_bytes((res.get("requests") or {}).get("memory"))
            lc = _parse_cpu_millicores((res.get("limits") or {}).get("cpu"))
            lm = _parse_mem_bytes((res.get("limits") or {}).get("memory"))
            if rc:
                req_cpu += rc; have_req = True
            if rm:
                req_mem += rm; have_req = True
            if lc:
                lim_cpu += lc; have_lim = True
            if lm:
                lim_mem += lm; have_lim = True

        usage_cpu = usage_mem = 0
        have_metrics = False
        try:
            mr = await c.get(
                f"{api}/apis/metrics.k8s.io/v1beta1/namespaces/{ns}/pods/{name}")
            if mr.status_code == 200:
                for ct in mr.json().get("containers") or []:
                    usage = ct.get("usage") or {}
                    cm = _parse_cpu_millicores(usage.get("cpu"))
                    mm = _parse_mem_bytes(usage.get("memory"))
                    if cm:
                        usage_cpu += cm; have_metrics = True
                    if mm:
                        usage_mem += mm; have_metrics = True
        except httpx.HTTPError as exc:
            log.debug("metrics-server read for pod %s failed: %s", name, exc)

    return {
        "id": name,
        "kind": "singleuser-pod",
        "workloadKind": "Pod (spawned)",
        "ephemeral": True,
        "namespace": ns,
        "deployment": name,
        "config": None,
        "storage": {"source": "kubelet /stats/summary", "haveStats": False,
                    "ephemeral": None, "volumes": []},
        "ready": "1/1" if ready else "0/1",
        "replicas": {"ready": 1 if ready else 0, "desired": 1},
        "images": images,
        "qosClass": st.get("qosClass"),
        "restarts": restarts,
        "pods": [{"name": name, "phase": st.get("phase"), "restarts": restarts,
                  "node": spec.get("nodeName"), "ready": ready}],
        "resources": {
            "haveMetrics": have_metrics,
            "cpu": {"usageMillicores": usage_cpu if have_metrics else None,
                    "requestMillicores": req_cpu if have_req else None,
                    "limitMillicores": lim_cpu if have_lim else None},
            "memory": {"usageBytes": usage_mem if have_metrics else None,
                       "requestBytes": req_mem if have_req else None,
                       "limitBytes": lim_mem if have_lim else None},
            "source": "metrics.k8s.io" if have_metrics else None,
        },
        "events": [],
        "red": None,
        "redNote": "Spawned JupyterHub lab pod: live per-pod CPU/memory is real "
                   "(metrics-server); request-rate RED needs the gateway /metrics "
                   "scrape.",
    }


async def _docker_inspect_all() -> list[dict[str, Any]]:
    """List running containers via the docker socket, with the fields the
    widget needs: name, image, state, networks, ports."""
    transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
    async with httpx.AsyncClient(transport=transport, base_url="http://docker") as c:
        r = await c.get("/containers/json")
        r.raise_for_status()
        out = []
        for c_ in r.json():
            networks = list((c_.get("NetworkSettings") or {}).get("Networks", {}).keys())
            name = (c_.get("Names") or ["?"])[0].lstrip("/")
            out.append({
                "id":       c_["Id"][:12],
                "name":     name,
                "image":    c_["Image"],
                "state":    c_["State"],
                "status":   c_["Status"],
                "networks": networks,
                "ports":    [(p.get("PublicPort"), p.get("PrivatePort"))
                             for p in (c_.get("Ports") or []) if p.get("PublicPort")],
            })
        return out


@router.get("/infra")
async def infra(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    _require_ops(user)

    # One host node, using this container's /proc/meminfo, /proc/loadavg and
    # CPU count as a proxy for the host.
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                meminfo[k.strip()] = v.strip()
        loadavg = open("/proc/loadavg").read().split()
        cores = os.cpu_count()
        node = {
            "id": "host-0", "kind": "host",
            "hostname": os.environ.get("HOST_HOSTNAME", "nhdw-demo"),
            "cores": cores,
            "memory_total_kib": int(meminfo.get("MemTotal", "0 kB").split()[0]),
            "memory_avail_kib": int(meminfo.get("MemAvailable", "0 kB").split()[0]),
            "load_1m":  float(loadavg[0]),
            "load_5m":  float(loadavg[1]),
            "load_15m": float(loadavg[2]),
        }
    except Exception as exc:
        log.warning("infra probe failed: %s", exc)
        node = {"id": "host-0", "kind": "host", "hostname": "unknown",
                 "error": str(exc)}
    return {
        "nodes": [node],
        "edges": [],   # single node, no edges
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/orchestration")
async def orchestration(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Pods and services from the Kubernetes API when it is configured;
    otherwise a docker container view."""
    _require_ops(user)

    if KUBE_API and KUBE_TOKEN:
        return await _orchestration_k8s()
    return await _orchestration_docker()


async def _orchestration_docker() -> dict[str, Any]:
    """Docker Compose view: each container is a node, with edges between
    containers that share nhdw_net or monitoring_net."""
    try:
        cs = await _docker_inspect_all()
    except Exception as exc:
        raise HTTPException(502, f"docker socket unreachable: {exc}")
    nodes = []
    for c in cs:
        kind = "container"
        if any(s in c["name"] for s in ("postgres", "mysql", "clickhouse", "minio")):
            kind = "datastore"
        elif any(s in c["name"] for s in ("gateway", "dashboard", "mlflow", "trino")):
            kind = "service"
        elif any(s in c["name"] for s in ("loki", "promtail", "prometheus", "grafana")):
            kind = "observability"
        nodes.append({
            "id":         c["id"],
            "label":      c["name"],
            "kind":       kind,
            "image":      c["image"],
            "state":      c["state"],
            "networks":   c["networks"],
            "ports":      c["ports"],
        })
    # Edges: undirected adjacency between containers on a shared network.
    by_net: dict[str, list[str]] = {}
    for c in cs:
        for net in c["networks"]:
            by_net.setdefault(net, []).append(c["id"])
    edges: list[dict[str, Any]] = []
    for net, ids in by_net.items():
        if net not in ("nhdw_net", "monitoring_net"): continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                edges.append({"source": ids[i], "target": ids[j],
                              "label": net, "kind": "network"})
    return {"source": "docker", "nodes": nodes, "edges": edges,
            "as_of": datetime.utcnow().isoformat() + "Z"}


async def _orchestration_k8s() -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {KUBE_TOKEN}"}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    # Scope to the gateway's namespace: its service-account Role is
    # namespace-scoped, so a cluster-wide LIST would not return these objects.
    ns = _kube_ns()
    async with httpx.AsyncClient(timeout=8, verify=False, headers=headers) as c:
        try:
            pods = (await c.get(f"{KUBE_API}/api/v1/namespaces/{ns}/pods")).json()
            svcs = (await c.get(f"{KUBE_API}/api/v1/namespaces/{ns}/services")).json()
        except Exception as exc:
            raise HTTPException(502, f"k8s api unreachable: {exc}")
        for p in pods.get("items", []):
            md = p.get("metadata", {}); st = p.get("status", {})
            nodes.append({
                "id":       f"pod/{md.get('namespace')}/{md.get('name')}",
                "label":    md.get("name"),
                "kind":     "pod",
                "namespace": md.get("namespace"),
                "state":    st.get("phase"),
                "labels":   md.get("labels") or {},
            })
        for s in svcs.get("items", []):
            md = s.get("metadata", {})
            sel = (s.get("spec") or {}).get("selector") or {}
            sid = f"svc/{md.get('namespace')}/{md.get('name')}"
            nodes.append({
                "id":       sid,
                "label":    md.get("name"),
                "kind":     "service",
                "namespace": md.get("namespace"),
                "selector": sel,
            })
            # Edge from the service to every pod whose labels match its selector.
            for n in nodes:
                if n["kind"] != "pod": continue
                if n["namespace"] != md.get("namespace"): continue
                if all(n["labels"].get(k) == v for k, v in sel.items()):
                    edges.append({"source": sid, "target": n["id"],
                                  "label": "selects", "kind": "selector"})
    return {"source": "k8s", "nodes": nodes, "edges": edges,
            "as_of": datetime.utcnow().isoformat() + "Z"}


@router.get("/graph")
async def graph(window: str = Query("5m"),
                user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Application topology, derived live on every call.

    Nodes are deployed application workloads (Deployments and StatefulSets).
    Jobs, CronJobs, tables, buckets, external sources and data-lineage edges
    are excluded; pipeline lineage is in dbt Docs.

    Edges are the wiring derived from each workload's container env, command,
    args and referenced ConfigMap connection strings, reconciled against the
    live scan (declared and observed, health, phantom). Per-edge traffic comes
    from the traffic provider registry.

    Shape: {nodes: [Node], edges: [Edge], as_of, meta: {traffic_sources, window}}.
    """
    _require_ops(user)

    api = _kube_api()
    token = _kube_token()
    if not api or not token:
        raise HTTPException(503, "no k8s API reachable from gateway")
    try:
        g = await topology_graph.derive_graph(
            api=api, token=token, ca=_kube_ca(), ns=_kube_ns())
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"k8s api unreachable: {exc}")

    # Per-node error-log count from Loki: one batched instant query merged into
    # node.signals.errLogs15m. If Loki is unavailable the key is absent.
    try:
        errs = await _loki_error_counts()
        for n in g.get("nodes", []):
            if n["id"] in errs:
                n.setdefault("signals", {})["errLogs15m"] = errs[n["id"]]
    except Exception as exc:                                   # noqa: BLE001
        log.debug("loki error-count merge skipped: %s", exc)

    # Traffic from the provider registry; the first provider wins per edge.
    _, window_label = traffic_providers.parse_window(window)
    traffic_by_edge: dict[str, dict[str, Any]] = {}
    sources: list[str] = []
    for prov in traffic_providers.PROVIDERS:
        blocks = await prov.load(window)
        if blocks:
            sources.append(prov.source)
        for eid, blk in blocks.items():
            traffic_by_edge.setdefault(eid, blk)   # first = highest precedence
    declared_ids = {e["id"] for e in g["edges"]}
    for e in g["edges"]:
        blk = traffic_by_edge.get(e["id"])
        if blk is not None:
            e["traffic"] = blk

    # Observed edges: flows a provider measured between two known nodes that are
    # not declared wiring (for example gateway->postgres). Adding them shows what
    # is talking now, not only what is declared. kind='observed' lets the UI
    # distinguish them from declared edges.
    node_ids = {n["id"] for n in g["nodes"]}
    for eid, blk in traffic_by_edge.items():
        if eid in declared_ids:
            continue
        src, _, dst = eid.partition("->")
        if src and dst and src in node_ids and dst in node_ids:
            g["edges"].append({
                "id": eid, "source": src, "target": dst,
                "kind": "observed", "observed": True, "traffic": blk,
            })

    return {
        "nodes": g["nodes"],
        "edges": g["edges"],
        "as_of": datetime.utcnow().isoformat() + "Z",
        "meta": {"traffic_sources": sources, "window": window_label},
    }


@router.get("/traffic")
async def traffic(window: str = Query("5m"),
                  user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Windowed per-edge RED metrics (reqRate, errRate, p95Ms, bytes, samples)
    from ops_audit.gateway_log, grouped by the path-to-edge map. The same query
    feeds /graph through GatewayLogTrafficProvider; this route exposes it for
    drill-down."""
    _require_ops(user)
    secs, window_label = traffic_providers.parse_window(window)
    try:
        edges = await traffic_providers.gateway_log_traffic(window)
    except Exception as exc:
        raise HTTPException(502, f"gateway_log unreachable: {exc}")
    return {
        "window": window_label,
        "window_seconds": secs,
        "edges": edges,
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/dataflow")
async def dataflow(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Deprecated. Returns an empty graph with a `deprecated` marker so older
    clients do not fail. Application topology is GET /api/topology/graph;
    data-pipeline lineage is in dbt Docs.
    """
    _require_ops(user)
    return {
        "nodes": [], "edges": [],
        "deprecated": True,
        "replaced_by": "/api/topology/graph",
        "note": "Replaced: apps -> /api/topology/graph, "
                "data pipeline -> dbt-docs + data-lineage.",
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/node")
async def node_detail(id: str = Query(...),
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Side-panel detail for a clicked topology node.

    For a /graph node (a Deployment or StatefulSet name) returns the full
    workload detail from _workload_detail, or the pod detail for a spawned
    JupyterHub lab pod. Orchestration-view ids (pod/..., svc/...) and the
    docker view return minimal metadata.
    """
    _require_ops(user)
    if id.startswith("pod/") or id.startswith("svc/"):
        # Orchestration-view ids: minimal metadata only.
        return {"id": id, "kind": "k8s", "note": "Run `kubectl describe` for full detail."}

    # /graph node: the id is the workload name.
    api = _kube_api()
    token = _kube_token()
    if api and token:
        try:
            det = await _workload_detail(id, _kube_ns())
            if det is not None:
                return det
            # No owning workload: possibly a spawned JupyterHub lab pod.
            pdet = await _pod_detail(id, _kube_ns())
            if pdet is not None:
                return pdet
        except httpx.HTTPError as exc:
            log.warning("node detail k8s enrich %s failed: %s", id, exc)
        # Not a workload: an external node such as fayda-replica, or not deployed.
        return {"id": id, "kind": "external",
                "note": "No live k8s workload in this namespace for this node "
                        "(external system or not currently deployed)."}

    # Docker fallback when no cluster API is configured (Compose setups).
    try:
        transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
        async with httpx.AsyncClient(transport=transport, base_url="http://docker") as c:
            r = await c.get(f"/containers/{id}/json")
            if r.status_code == 200:
                return r.json()
    except Exception as exc:
        log.warning("docker inspect %s failed: %s", id, exc)
    return {"id": id, "note": "no detail available"}


LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
_ERR_REGEX = "(?i)(error|exception|traceback|fatal)"


async def _loki_error_counts(window: str = "15m") -> dict[str, int]:
    """One batched LogQL instant query counting error-like log lines per
    workload over the window, keyed by the promtail `app` label (equal to the
    topology node id). Raises on any failure; callers treat it as optional."""
    expr = (f'sum by (app) (count_over_time('
            f'{{namespace="nhdw"}} |~ `{_ERR_REGEX}` [{window}]))')
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(f"{LOKI_URL}/loki/api/v1/query", params={"query": expr})
        r.raise_for_status()
        body = r.json()
    out: dict[str, int] = {}
    for res in (body.get("data") or {}).get("result") or []:
        app = (res.get("metric") or {}).get("app")
        try:
            v = int(float(res["value"][1]))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if app and v > 0:
            out[app] = v
    return out


@router.get("/errors")
async def node_errors(id: str = Query(...),
                      window: str = Query("1h"),
                      limit: int = Query(25, ge=1, le=200),
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Recent error-like log lines for one workload from Loki, behind the node's
    error badge. Unlike /logs (the kubelet's current log), this searches
    retained history. Serves the topology widget; Loki is not exposed directly."""
    _require_ops(user)
    secs, label = traffic_providers.parse_window(window)
    import time as _time
    end_ns = int(_time.time() * 1e9)
    start_ns = end_ns - secs * 10**9
    params = {
        "query": f'{{namespace="nhdw", app="{id}"}} |~ `{_ERR_REGEX}`',
        "start": str(start_ns), "end": str(end_ns),
        "limit": str(limit), "direction": "backward",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params)
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as exc:
        return {"id": id, "window": label, "lines": [],
                "note": f"Loki unreachable: {exc}"}
    lines: list[dict[str, Any]] = []
    for stream in (body.get("data") or {}).get("result") or []:
        pod = (stream.get("stream") or {}).get("pod", "")
        for ts, line in stream.get("values") or []:
            lines.append({"ts": int(ts) // 10**6, "pod": pod, "line": line[:500]})
    lines.sort(key=lambda x: -x["ts"])
    return {"id": id, "window": label, "lines": lines[:limit]}


@router.get("/logs")
async def node_logs(id: str = Query(...),
                    container: str | None = Query(None),
                    tail: int = Query(150, ge=1, le=1000),
                    user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Tail the container log of a topology node's workload through the pod-log
    subresource (``GET .../pods/{pod}/log?tailLines=...&container=...``, which
    needs pods/log RBAC). Resolves the workload's first Running pod and returns
    its container list (for the panel's selector) and the last ``tail`` lines
    of ``container`` (default: the first container).

    This is the current kubelet log; /errors searches retained history in Loki."""
    _require_ops(user)
    api = _kube_api()
    token = _kube_token()
    if not api or not token:
        raise HTTPException(503, "no k8s API reachable from gateway")
    ns = _kube_ns()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=10, verify=_kube_ca(), headers=headers) as c:
            pod, containers = await _first_pod_for_workload(c, api, ns, id)
            if not pod:
                return {"id": id, "pod": None, "containers": containers,
                        "container": None, "tail": tail, "lines": [],
                        "note": "No running pod for this workload "
                                "(external system or not currently deployed)."}
            cont = container if (container and container in containers) \
                else (containers[0] if containers else None)
            params = {"tailLines": str(tail), "timestamps": "false"}
            if cont:
                params["container"] = cont
            lr = await c.get(f"{api}/api/v1/namespaces/{ns}/pods/{pod}/log", params=params)
            if lr.status_code != 200:
                return {"id": id, "pod": pod, "containers": containers,
                        "container": cont, "tail": tail, "lines": [],
                        "note": f"kubelet log read failed (HTTP {lr.status_code})."}
            lines = (lr.text or "").splitlines()[-tail:]
            return {"id": id, "pod": pod, "containers": containers,
                    "container": cont, "tail": tail, "lines": lines}
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"k8s api unreachable: {exc}")


@router.get("/cluster")
async def cluster_summary(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Live cluster health for the topology summary:
      - pods by phase (Running, Pending, Succeeded, Failed, Unknown) in the namespace
      - per-node CPU and memory, used (metrics-server) against allocatable
      - HPA status: current and desired replicas, CPU utilisation (autoscaling/v2)
    Each part is best effort; per-service RED and history are not included."""
    _require_ops(user)
    api = _kube_api()
    token = _kube_token()
    if not api or not token:
        raise HTTPException(503, "no k8s API reachable from gateway")
    ns = _kube_ns()
    headers = {"Authorization": f"Bearer {token}"}
    phases: dict[str, int] = {"Running": 0, "Pending": 0, "Succeeded": 0,
                              "Failed": 0, "Unknown": 0}
    node_rows: list[dict[str, Any]] = []
    hpa_rows: list[dict[str, Any]] = []
    usage: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=8, verify=_kube_ca(), headers=headers) as c:
        # pods by phase (namespace)
        try:
            pr = await c.get(f"{api}/api/v1/namespaces/{ns}/pods")
            if pr.status_code == 200:
                for p in pr.json().get("items", []):
                    ph = (p.get("status") or {}).get("phase") or "Unknown"
                    phases[ph] = phases.get(ph, 0) + 1
        except httpx.HTTPError as exc:
            log.debug("cluster pods read failed: %s", exc)

        # node allocatable (core v1; needs nodes get/list RBAC)
        alloc: dict[str, dict[str, Any]] = {}
        try:
            nr = await c.get(f"{api}/api/v1/nodes")
            if nr.status_code == 200:
                for n in nr.json().get("items", []):
                    nm = (n.get("metadata") or {}).get("name")
                    a = (n.get("status") or {}).get("allocatable") or {}
                    alloc[nm] = {"cpu": _parse_cpu_millicores(a.get("cpu")),
                                 "mem": _parse_mem_bytes(a.get("memory"))}
        except httpx.HTTPError as exc:
            log.debug("cluster nodes read failed: %s", exc)

        # node usage (metrics-server)
        try:
            mr = await c.get(f"{api}/apis/metrics.k8s.io/v1beta1/nodes")
            if mr.status_code == 200:
                for m in mr.json().get("items", []):
                    nm = (m.get("metadata") or {}).get("name")
                    u = m.get("usage") or {}
                    usage[nm] = {"cpu": _parse_cpu_millicores(u.get("cpu")),
                                 "mem": _parse_mem_bytes(u.get("memory"))}
        except httpx.HTTPError as exc:
            log.debug("cluster node metrics read failed: %s", exc)

        for nm in sorted(set(alloc) | set(usage)):
            a = alloc.get(nm, {})
            u = usage.get(nm, {})
            node_rows.append({
                "name": nm,
                "cpu": {"usedMillicores": u.get("cpu"),
                        "allocatableMillicores": a.get("cpu")},
                "memory": {"usedBytes": u.get("mem"),
                           "allocatableBytes": a.get("mem")},
            })

        # HPAs (autoscaling/v2)
        try:
            hr = await c.get(f"{api}/apis/autoscaling/v2/namespaces/{ns}/horizontalpodautoscalers")
            if hr.status_code == 200:
                for h in hr.json().get("items", []):
                    hs = h.get("status") or {}
                    hspec = h.get("spec") or {}
                    cur_cpu = next((((cm.get("resource") or {}).get("current") or {})
                                    .get("averageUtilization")
                                    for cm in hs.get("currentMetrics") or []
                                    if (cm.get("resource") or {}).get("name") == "cpu"), None)
                    tgt_cpu = next((((tm.get("resource") or {}).get("target") or {})
                                    .get("averageUtilization")
                                    for tm in hspec.get("metrics") or []
                                    if (tm.get("resource") or {}).get("name") == "cpu"), None)
                    hpa_rows.append({
                        "name": (h.get("metadata") or {}).get("name"),
                        "target": (hspec.get("scaleTargetRef") or {}).get("name"),
                        "current": hs.get("currentReplicas"),
                        "desired": hs.get("desiredReplicas"),
                        "min": hspec.get("minReplicas"),
                        "max": hspec.get("maxReplicas"),
                        "currentCpuPct": cur_cpu,
                        "targetCpuPct": tgt_cpu,
                    })
        except httpx.HTTPError as exc:
            log.debug("cluster hpa read failed: %s", exc)

    return {
        "podsByPhase": phases,
        "podTotal": sum(phases.values()),
        "nodes": node_rows,
        "hpas": hpa_rows,
        "haveNodeMetrics": bool(usage),
        "source": "k8s API + metrics-server",
        "as_of": datetime.utcnow().isoformat() + "Z",
    }


# Live Deployment scaling
@router.post("/scale")
async def scale_deployment(body: dict, user: UserCtx = Depends(get_user)) -> dict:
    """Set replicas on a Deployment (default: the gateway's namespace).

    Body: { deployment: <name>, replicas: <int> [, namespace: <ns>] }

    Restricted to R-OPS-* and R-SUPER. The gateway calls the Kubernetes API
    with its in-cluster service account; gateway-rbac.yaml grants patch on
    deployments/scale in the nhdw namespace only. Replicas are clamped to 0..20.
    """
    _require_ops(user)
    name = str(body.get("deployment") or "").strip()
    replicas = body.get("replicas")
    if not name or replicas is None:
        raise HTTPException(400, "body needs {deployment, replicas}")
    try:
        replicas = max(0, min(20, int(replicas)))  # hard cap
    except (TypeError, ValueError):
        raise HTTPException(400, f"replicas must be an integer, got {replicas!r}")
    ns = str(body.get("namespace") or _kube_ns())

    api_url = _kube_api()
    if not api_url:
        raise HTTPException(503, "no k8s API reachable from gateway")
    token = _kube_token()
    if not token:
        raise HTTPException(503, "no SA token mounted")

    url = f"{api_url}/apis/apps/v1/namespaces/{ns}/deployments/{name}/scale"
    patch = {"spec": {"replicas": replicas}}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/strategic-merge-patch+json",
    }
    try:
        async with httpx.AsyncClient(timeout=8, verify=_kube_ca()) as c:
            r = await c.patch(url, json=patch, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(502, f"k8s api unreachable: {exc}")
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"k8s rejected scale: {r.text[:300]}")
    out = r.json()
    return {
        "deployment": name,
        "namespace":  ns,
        "replicas":   out.get("spec", {}).get("replicas", replicas),
        "status":     out.get("status", {}).get("replicas"),
        "actor":      {"kc_sub": user.kc_sub, "role": user.primary_role},
    }
