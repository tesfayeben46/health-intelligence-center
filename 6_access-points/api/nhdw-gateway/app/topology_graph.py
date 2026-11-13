"""Structural and live derivation for GET /api/topology/graph.

Nodes are the deployed application workloads in the namespace (Deployments and
StatefulSets). Jobs and CronJobs (ingestors, dbt, bridge-sync, ml-runner),
database tables and schemas, object-store buckets, external source systems and
data-lineage edges are excluded; dbt-docs and the data-lineage page cover those.
Storage applications (postgres-vault, seaweedfs-filer, clickhouse) appear as
nodes without their internal tables or buckets.

JupyterHub single-user lab pods (KubeSpawner, labels app=jupyterhub and
component=singleuser-server) are the one exception to "declared workloads
only": each running lab is a compute node marked ``ephemeral=True`` (observed,
not declared in a manifest) with a hub-to-lab ``spawns`` edge, so operators see
labs start and stop. Every other node has ``ephemeral=False``.

Edges are the declared wiring, derived on every call from each workload's own
container spec:

  * plain ``value:`` env DNS references (secretKeyRef values are not read; the
  manifest convention keeps every host a plain value and every password a
  secretKeyRef),
  * ``command`` / ``args`` (e.g. keycloak's wait-for-db psql, oauth2-proxy's
    redeem/jwks URLs), with ``$(VAR)`` / ``${VAR}`` interpolation resolved
    against the workload's own plain env,
  * referenced ConfigMap connection strings (e.g. the trino-catalog
    ``connection-url=jdbc:postgresql://postgres-vault:5432/...``).

Host tokens are matched against a lookup of every Service's
{short-name, ``<svc>.nhdw`` , ``<svc>.nhdw.svc`` , ``<svc>.nhdw.svc.cluster.local``}
form, mapped to the workload that Service selects. Edge ``kind`` (http, db,
s3, smtp, native, oidc) is inferred from the target service and a small
port-to-protocol map.

Off-cluster dependencies reached through the k3d host route are not shown as
nodes: the map is an in-cluster view. This covers the gateway's
``OLLAMA_URL=host.k3d.internal:11434`` LLM (0_external/llm/) and the public
dashboard's ``FAYDA_INTERNAL_BASE=host.k3d.internal:8096`` auth replica
(0_external/fayda). All other ``host.k3d.internal`` references are in the
excluded ingestor Jobs.

The live Kubernetes scan (the same LIST the orchestration view uses) reconciles
every node: declared against observed, health from ready-replica counts, and
phantom when a workload is declared but has no running pod. Traffic is added by
the provider registry in ``traffic_providers.py``; this module never reads the
audit DB.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger("gateway.topology.graph")


# Id helpers shared by the structural derivation and every traffic provider,
# so both produce identical ids; a mismatch would silently drop traffic.
def node_id(workload_name: str) -> str:
    return workload_name


def edge_id(src: str, dst: str) -> str:
    return f"{node_id(src)}->{node_id(dst)}"


# Node kind, keyed on the workload's `tier:` label.
KIND_BY_TIER: dict[str, str] = {
    "api":          "api",
    "storage":      "datastore",
    "security":     "security",
    "tool-sso":     "security",   # the tool-gate OIDC reverse proxies
    "bi":           "ui",         # Superset: self-service SQL and BI
    "ml":           "ml",
    "dashboard":    "ui",
    "orchestrator": "orchestrator",
    "platform":     "platform",
    "ops":          "ops",
    "compute":      "engine",
    "external":     "external",
}


# Edge kind inference.
# Storage and auth targets have a fixed protocol regardless of the port the
# caller used. Everything else falls back to a small port-to-protocol map
# (which also maps SeaweedFS's S3 port 8333), then to plain http.
SPECIAL_TARGET_KIND: dict[str, str] = {
    "keycloak":       "oidc",
    "seaweedfs-filer": "s3",   # SeaweedFS filer runs the S3 gateway (Service seaweedfs-s3:8333)
    "clickhouse":     "native",
    "postgres-vault": "db",
    "fayda-replica":  "oidc",
    "kubernetes-api": "k8s",
}


# Per-node subtitle: an optional one-line description the renderer shows under
# the node name and in the detail panel. Keyed on the `tier:` label, with
# name-specific lines for a few workloads.
SUBTITLE_BY_TIER: dict[str, str] = {
    "api": "API, data gateway",
    "tool-sso": "SSO gate, role-aware proxy",
    "bi": "self-service SQL/BI",
}
SUBTITLE_BY_NAME: dict[str, str] = {
    "nhdw-gateway":   "API, data gateway",
    "kubernetes-api": "control plane",
}


def node_subtitle(name: str, tier: str) -> str | None:
    return SUBTITLE_BY_NAME.get(name) or SUBTITLE_BY_TIER.get(tier)


# Placement zone (the architecture band a node sits in). The admin renderer
# draws the zone rectangles and titles; the zone each node belongs to is decided
# here. The API gateway has its own "apis" band. Zones come from the tier, with
# per-node overrides (the ops console, the synthesised control plane, the
# external providers). The renderer keeps the same fallback, so a graph payload
# without `zone` lays out identically.
_TIER_ZONE: dict[str, str] = {
    "dashboard": "access", "api": "apis",
    "storage": "storage",
    "compute": "compute", "engine": "compute", "ml": "compute", "serving": "compute",
    "orchestrator": "compute", "platform": "compute",
    "security": "security", "external": "security",
    "tool-sso": "security",   # the tool-gate OIDC reverse proxies sit with auth
    "bi": "access",           # Superset sits with the access endpoints
    "ops": "observability", "observability": "observability",
}
_NODE_ZONE: dict[str, str] = {
    "headlamp": "observability",
    "kubernetes-api": "observability",
    "fayda-replica": "security",
    "nhdw-ollama": "compute",
}


def zone_for(name: str, tier: str) -> str:
    return _NODE_ZONE.get(name) or _TIER_ZONE.get(tier) or "compute"
PORT_KIND: dict[int, str] = {
    5432: "db",
    8025: "http",
    80:   "http",
    8000: "http",
    8080: "http",
    8181: "http",
    5000: "http",
    11434: "http",
    4180: "http",
    3100: "http",
    9000: "native",   # clickhouse native TCP
    8333: "s3",       # seaweedfs S3 gateway
    8123: "native",   # clickhouse http
    8096: "oidc",     # fayda replica auth
}


def edge_kind(target: str, port: int | None) -> str:
    if target in SPECIAL_TARGET_KIND:
        return SPECIAL_TARGET_KIND[target]
    if port is not None and port in PORT_KIND:
        return PORT_KIND[port]
    return "http"


# fayda-replica: external eSignet-style auth, reached via the k3d host route.
_FAYDA_HOST = "host.k3d.internal"
_FAYDA_PORT = 8096
_FAYDA_NODE = "fayda-replica"

# nhdw-ollama: the external LLM (0_external/llm/) the gateway calls for the dashboard
# assistant, reached via the same k3d host route (OLLAMA_URL=host.k3d.internal:11434).
_LLM_HOST = "host.k3d.internal"
_LLM_PORT = 11434
_LLM_NODE = "nhdw-ollama"

_VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


# Low-level Kubernetes GET
async def _get(client: httpx.AsyncClient, api: str, path: str) -> dict[str, Any]:
    r = await client.get(f"{api}{path}")
    r.raise_for_status()
    return r.json()


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Resolve $(VAR), ${VAR} and $VAR against the workload's plain env, so a
    host behind an env reference (as in airflow's SQL_ALCHEMY_CONN) still
    resolves. The Kubernetes `$(VAR)` form is rewritten to `${VAR}` before the
    regex runs."""
    value = value.replace("$(", "${").replace(")", "}") if "$(" in value else value
    return _VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), value)


def _scan_texts(spec: dict[str, Any],
                configmaps: dict[str, dict[str, str]]) -> list[str]:
    """Every host-bearing string a workload declares: plain env values, command
    and args (init and main containers), and the string values of every
    ConfigMap it references. secretKeyRef env is skipped; it carries no host."""
    pod = (((spec.get("spec") or {}).get("template") or {}).get("spec")) or {}
    containers = (pod.get("initContainers") or []) + (pod.get("containers") or [])

    # workload-wide plain env map for $(VAR) resolution
    env_map: dict[str, str] = {}
    for c in containers:
        for e in c.get("env") or []:
            if "value" in e and e.get("name"):
                env_map[e["name"]] = str(e["value"])

    texts: list[str] = []
    for c in containers:
        for e in c.get("env") or []:
            if "value" in e:
                texts.append(_interpolate(str(e["value"]), env_map))
        for key in ("command", "args"):
            for part in c.get(key) or []:
                texts.append(_interpolate(str(part), env_map))

    # ConfigMaps referenced via volumes[].configMap or envFrom[].configMapRef
    cm_names: set[str] = set()
    for v in pod.get("volumes") or []:
        cm = (v.get("configMap") or {}).get("name")
        if cm:
            cm_names.add(cm)
    for c in containers:
        for ef in c.get("envFrom") or []:
            cm = (ef.get("configMapRef") or {}).get("name")
            if cm:
                cm_names.add(cm)
    for cm in cm_names:
        for val in (configmaps.get(cm) or {}).values():
            texts.append(str(val))

    return texts


def _labels_match(pod_labels: dict[str, str], selector: dict[str, str]) -> bool:
    return bool(selector) and all(pod_labels.get(k) == v for k, v in selector.items())


def _host_matches(text: str, short: str) -> list[int | None]:
    """Return the ports at which `short` (a service short name) is referenced as
    a host in `text`, either as the short name or as any
    <short>.nhdw[.svc[.cluster.local]] form, with an optional :port. An empty
    list means not referenced; a None entry means referenced without a port.

    A match counts only if it carries a connection signal: an explicit
    ``:port``, the ``.nhdw`` FQDN suffix, or ``//`` or ``@`` (URL scheme or
    userinfo) immediately before it. Bare service-name-shaped tokens (an OIDC
    clientId ``admin`` in a realm export, a schema name ``keycloak`` in a DDL
    bundle, a word in a comment) are ignored. Every real wiring reference in
    the manifests carries one of these signals."""
    pat = re.compile(
        r"(?<![\w.-])" + re.escape(short) +
        r"(?P<fqdn>\.nhdw(?:\.svc(?:\.cluster\.local)?)?)?"
        r"(?::(?P<port>\d+))?"
        r"(?![\w-])"
    )
    ports: list[int | None] = []
    for m in pat.finditer(text):
        port = int(m.group("port")) if m.group("port") else None
        pre2 = text[max(0, m.start() - 2):m.start()]
        signal = (port is not None
                  or m.group("fqdn") is not None
                  or pre2.endswith("//") or pre2.endswith("@"))
        if signal:
            ports.append(port)
    return ports


async def derive_graph(*, api: str, token: str, ca: str | bool,
                       ns: str) -> dict[str, Any]:
    """Build the application {nodes, edges} graph from live Kubernetes. The
    caller attaches traffic, as_of and meta. Raises if the API is unreachable."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=8, verify=ca, headers=headers) as c:
        deploys = (await _get(c, api, f"/apis/apps/v1/namespaces/{ns}/deployments")).get("items", [])
        stses   = (await _get(c, api, f"/apis/apps/v1/namespaces/{ns}/statefulsets")).get("items", [])
        pods    = (await _get(c, api, f"/api/v1/namespaces/{ns}/pods")).get("items", [])
        svcs    = (await _get(c, api, f"/api/v1/namespaces/{ns}/services")).get("items", [])

        workloads = [("Deployment", d) for d in deploys] + \
                    [("StatefulSet", s) for s in stses]

        # Fetch every ConfigMap referenced by any workload once, for the scan.
        wanted_cms: set[str] = set()
        for _, w in workloads:
            pod = (((w.get("spec") or {}).get("template") or {}).get("spec")) or {}
            for v in pod.get("volumes") or []:
                cm = (v.get("configMap") or {}).get("name")
                if cm:
                    wanted_cms.add(cm)
            for cont in (pod.get("initContainers") or []) + (pod.get("containers") or []):
                for ef in cont.get("envFrom") or []:
                    cm = (ef.get("configMapRef") or {}).get("name")
                    if cm:
                        wanted_cms.add(cm)
        configmaps: dict[str, dict[str, str]] = {}
        for name in wanted_cms:
            try:
                cm = await _get(c, api, f"/api/v1/namespaces/{ns}/configmaps/{name}")
                configmaps[name] = cm.get("data") or {}
            except Exception as exc:
                log.debug("configmap %s not readable: %s", name, exc)

    # Nodes and live reconciliation
    nodes: dict[str, dict[str, Any]] = {}
    wl_meta: dict[str, dict[str, Any]] = {}   # node_id: {selector, spec, tmpl_labels}

    for kind_k8s, w in workloads:
        md = w.get("metadata") or {}
        name = md.get("name")
        if not name:
            continue
        nid = node_id(name)
        spec = w.get("spec") or {}
        tmpl_labels = (((spec.get("template") or {}).get("metadata") or {}).get("labels")) or {}
        tier = (md.get("labels") or {}).get("tier") or tmpl_labels.get("tier") or "unknown"
        selector = (spec.get("selector") or {}).get("matchLabels") or {}

        # live health from pods matching this workload's selector
        wl_pods = [p for p in pods
                   if _labels_match((p.get("metadata") or {}).get("labels") or {}, selector)]
        desired = spec.get("replicas")
        desired = 1 if desired is None else int(desired)
        ready = int((w.get("status") or {}).get("readyReplicas") or 0)
        crashloop = any(
            (cs.get("state") or {}).get("waiting", {}).get("reason") == "CrashLoopBackOff"
            for p in wl_pods
            for cs in (p.get("status") or {}).get("containerStatuses") or []
        )
        observed = len(wl_pods) >= 1
        # Restart pressure and OOM kills from the same pod scan: per-node
        # distress signals with no extra API calls.
        restarts = sum(int(cs.get("restartCount") or 0)
                       for p in wl_pods
                       for cs in (p.get("status") or {}).get("containerStatuses") or [])
        oom_killed = any(
            (((cs.get("lastState") or {}).get("terminated") or {}).get("reason")) == "OOMKilled"
            for p in wl_pods
            for cs in (p.get("status") or {}).get("containerStatuses") or []
        )
        if crashloop:
            health = "down"
        elif desired == 0:
            health = "unknown"
        elif ready >= desired and ready > 0:
            health = "up"
        elif 0 < ready < desired:
            health = "degraded"
        elif ready == 0:
            health = "down"
        else:
            health = "unknown"

        nodes[nid] = {
            "id": nid,
            "label": name,
            "subtitle": node_subtitle(name, tier),
            "tier": tier,
            "zone": zone_for(name, tier),
            "kind": KIND_BY_TIER.get(tier, "service"),
            "declared": True,
            "observed": observed,
            "ephemeral": False,          # a declared workload, not a spawned pod
            "health": health,
            "phantom": (not observed),   # declared here, no live pod
            "k8s": {"deployment": name, "ready": f"{ready}/{desired}", "namespace": ns},
            # Distress signals. The graph route merges errLogs15m from Loki
            # (fail-soft); it is absent when Loki is unreachable.
            "signals": {"restarts": restarts, "oomKilled": oom_killed},
        }
        wl_meta[nid] = {"selector": selector, "spec": w, "tmpl_labels": tmpl_labels}

    # Service host alias to owning node: short name -> node_id (the
    # workload the Service selects). _host_matches accepts every FQDN form of
    # the short name.
    alias_to_node: dict[str, str] = {}
    alias_default_port: dict[str, int | None] = {}
    for s in svcs:
        smd = s.get("metadata") or {}
        sname = smd.get("name")
        sel = (s.get("spec") or {}).get("selector") or {}
        if not sname or not sel:
            continue
        owner = None
        for nid, meta in wl_meta.items():
            if _labels_match(meta["tmpl_labels"], sel):
                owner = nid
                break
        if owner is None:
            continue
        ports = (s.get("spec") or {}).get("ports") or []
        alias_to_node[sname] = owner
        alias_default_port[sname] = int(ports[0]["port"]) if ports else None

    # Edges from the declared wiring of every workload
    edges: dict[str, dict[str, Any]] = {}

    def _add_edge(src: str, dst: str, port: int | None) -> None:
        if src == dst:
            return
        eid = edge_id(src, dst)
        if eid in edges:
            return
        s_node, d_node = nodes.get(src), nodes.get(dst)
        s_h = s_node["health"] if s_node else "unknown"
        d_h = d_node["health"] if d_node else "unknown"
        if "down" in (s_h, d_h):
            status = "down"
        elif "degraded" in (s_h, d_h):
            status = "degraded"
        elif s_h == "up" and d_h == "up":
            status = "up"
        else:
            status = "unknown"
        edges[eid] = {
            "id": eid, "source": src, "target": dst,
            "kind": edge_kind(dst, port),
            "declared": True,
            "observed": bool(s_node and s_node["observed"] and d_node and d_node["observed"]),
            "status": status,
            "traffic": None,
        }

    for nid, meta in wl_meta.items():
        texts = _scan_texts(meta["spec"], configmaps)
        blob = "\n".join(texts)
        # in-cluster service refs
        for alias, target in alias_to_node.items():
            if target == nid:
                continue
            found_ports = _host_matches(blob, alias)
            if not found_ports:
                continue
            port = next((p for p in found_ports if p is not None), None)
            if port is None:
                port = alias_default_port.get(alias)
            _add_edge(nid, target, port)
        # Off-cluster dependencies (the fayda-replica identity service and the
        # nhdw-ollama LLM, both reached via host.k3d.internal, and the external
        # SMTP relay) are not nodes: the topology represents the Kubernetes
        # cluster, so no edge is drawn to them.

    # Kubernetes control plane. The `kubernetes` Service is in the `default`
    # namespace, outside the nhdw-scoped scan, so the API server is never a
    # scanned node. Synthesise a `kubernetes-api` node and connect the two
    # workloads that call it: headlamp (the ops console, whose only peer is the
    # API) and nhdw-gateway (the /api/topology and scale calls this module uses).
    k8s_clients = [c for c in ("headlamp", "nhdw-gateway") if c in nodes]
    if k8s_clients:
        nodes["kubernetes-api"] = {
            "id": "kubernetes-api",
            "label": "kubernetes-api",
            "subtitle": node_subtitle("kubernetes-api", "platform"),
            "tier": "platform",
            "zone": zone_for("kubernetes-api", "platform"),
            "kind": "platform",
            "declared": True,
            "observed": True,          # the gateway just reached it to build this graph
            "ephemeral": False,
            "health": "up",
            "phantom": False,
            "k8s": None,
        }
        for client in k8s_clients:
            _add_edge(client, "kubernetes-api", 443)

    # JupyterHub single-user lab pods, spawned on demand.
    # KubeSpawner launches one lab pod per user (labels app=jupyterhub,
    # component=singleuser-server, hub.jupyter.org/username=<user>). They are
    # not declared workloads, so the scan above does not see them; each becomes
    # an ephemeral compute node, with a hub-to-lab "spawns" edge from the hub
    # Deployment (component=hub). Ephemeral is not phantom: the pod is observed
    # and running, only not declared in a manifest.
    for p in pods:
        pmd = p.get("metadata") or {}
        plabels = pmd.get("labels") or {}
        if plabels.get("app") != "jupyterhub" or plabels.get("component") != "singleuser-server":
            continue
        pname = pmd.get("name")
        if not pname or pname in nodes:
            continue
        pst = p.get("status") or {}
        phase = pst.get("phase") or "Unknown"
        cs_list = pst.get("containerStatuses") or []
        ready = bool(cs_list) and all(cs.get("ready") for cs in cs_list)
        if phase == "Running":
            health = "up" if ready else "degraded"
        elif phase == "Pending":
            health = "degraded"
        elif phase == "Failed":
            health = "down"
        else:
            health = "unknown"
        user = plabels.get("hub.jupyter.org/username") or pname
        nodes[pname] = {
            "id": pname,
            "label": pname,
            "subtitle": f"spawned lab, {user}",
            "tier": "compute",
            "zone": zone_for(pname, "compute"),   # compute band
            "kind": "notebook",
            "declared": False,        # spawned on demand, not a manifest workload
            "observed": True,
            "ephemeral": True,        # dynamically spawned; rendered distinctly
            "health": health,
            "phantom": False,
            "k8s": {"deployment": pname, "ready": "1/1" if ready else "0/1",
                    "namespace": ns, "pod": pname},
        }
        if "jupyterhub" in nodes:
            eid = edge_id("jupyterhub", pname)
            if eid not in edges:
                edges[eid] = {
                    "id": eid, "source": "jupyterhub", "target": pname,
                    "kind": "spawns", "declared": False, "observed": True,
                    "status": "up" if health == "up" else "degraded",
                    "traffic": None,
                }

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
