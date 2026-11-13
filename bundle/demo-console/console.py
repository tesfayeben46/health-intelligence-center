#!/usr/bin/env python3
"""NHDW Demo Console: the bundle's host-run operator console.

One dependency-free (Python 3.9+ stdlib only) web server that:

  1. Serves the Demo Control Room SPA (the catalog of demo points) statically at /
     from ./dist (built by build-spa.sh). The SPA is anonymous and calls only
     same-origin /api/demo/*.
  2. Implements the /api/demo/* backend those calls need (pages / scenarios /
     credentials / trigger / reload), with triggers going to the gateway's public
     ingress (https://gateway.127.0.0.1.nip.io, self-signed) instead of in-cluster DNS.
  3. Serves the /api/console/* controller behind the SPA's "System · Console" page:
     Rebuild/Teardown with live progress streamed over SSE, parsed from the
     scripts' own step markers; the scripts themselves are unmodified.

It runs outside Kubernetes because the cluster contains only what ships in a
production release. The console supports demonstrations (like the external
source replicas), and the controller manages the cluster's own lifecycle, so it
cannot run inside it.

Usage:  ./run.sh          (or: python3 console.py [--port 8500])
Then open http://localhost:8500/ -> System · Console
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent                      # bundle/demo-console -> repo root
DIST_DIR = HERE / "dist"
STATIC_DIR = HERE / "static"
SCENARIOS_PATH = Path(os.environ.get(
    "DEMO_SCENARIOS_PATH",
    str(REPO / "bundle/seeds/demo/scenarios.json")))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "https://gateway.127.0.0.1.nip.io")
SCRIPTS = REPO / "bundle/scripts"

# The gateway ingress uses a self-signed cert (demo only), so skip verification.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ─── demo catalog ───────────────────────────────────────────────────────────
_cat_lock = threading.Lock()
_catalog: dict | None = None


def _load_catalog(force: bool = False) -> dict:
    global _catalog
    with _cat_lock:
        if _catalog is None or force:
            if SCENARIOS_PATH.exists():
                doc = json.loads(SCENARIOS_PATH.read_text())
            else:
                doc = {}
            _catalog = {
                "scenarios":   doc.get("scenarios") or [],
                "credentials": doc.get("credentials") or [],
                "pages":       doc.get("pages") or [],
            }
        return _catalog


# Stable per-role mock UUIDs. They must match bundle/seed-pack/app/security/users.yaml:
# audit trails stay coherent, and inserts such as PAT issuance have an FK onto
# ops_identity."user".kc_sub, so a uuid that drifts from the seed returns a 500.
_ROLE_USERS = {
    "R-SUPER":     "dddddddd-eeee-ffff-0000-111111111111",  # root
    "R-OPS-ADM":   "11111111-2222-3333-4444-555555555555",  # hana.d
    "R-DEC":       "44444444-5555-6666-7777-888888888888",  # tadesse.h
    "R-CONS-ANA":  "22222222-3333-4444-5555-666666666666",  # selam.t
    "R-CONS-SCI":  "77777777-8888-9999-aaaa-bbbbbbbbbbbb",  # tigist.bekele
    "R-STEW":      "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",  # demo.steward
    "R-PUB-ANON":  "00000000-0000-0000-0000-000000000000",  # anonymous (unseeded by design)
}


def _gateway(method: str, path: str, *, role: str, body: dict | None = None,
             params: str = "", timeout: int = 12) -> dict:
    url = f"{GATEWAY_URL}{path}{params}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "X-Mock-User": _ROLE_USERS.get(role, _ROLE_USERS["R-PUB-ANON"]),
        "X-Mock-Role": role,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:      # 4xx/5xx still carry a JSON body
        raw, status = e.read(), e.code
    except Exception as exc:                                       # noqa: BLE001
        return {"status": 0, "body": {"error": f"gateway unreachable: {exc}"}}
    try:
        parsed = json.loads(raw)
    except Exception:                                              # noqa: BLE001
        parsed = {"raw": raw[:500].decode(errors="replace")}
    return {"status": status, "body": parsed}


def _trigger(kind: str, args: dict) -> dict:
    role = args.get("role", "R-DEC")
    if kind == "pat_issue":
        out = _gateway("POST", "/api/api-tokens", role=role,
                       body={"name": args.get("name", f"demo-{role.lower()}"),
                             "expires_in_days": 1})
        return out
    if kind == "mart_read":
        schema, table = args.get("schema", "marts"), args.get(
            "table", "bi_programme_hiv_cascade_total")
        out = _gateway("GET", f"/api/marts/{schema}/{table}", role=role,
                       params="?_limit=3")
        out.update(role=role, mart=f"{schema}.{table}")
        return out
    if kind == "k8s_scale":
        role = args.get("role", "R-OPS-ADM")
        dep, replicas = args.get("deployment", "internal-bi"), int(args.get("replicas", 1))
        out = _gateway("POST", "/api/topology/scale", role=role,
                       body={"deployment": dep, "replicas": replicas})
        out.update(deployment=dep, replicas_requested=replicas)
        return out
    if kind == "invalidate":
        _load_catalog(force=True)
        return {"status": "reloaded"}
    if kind == "opa_decide":
        # Dead in the shipped catalog; OPA has no host-reachable route by design.
        return {"status": 501, "body": {"error": "opa_decide is not available from "
                                                 "the host console (no OPA ingress); "
                                                 "no shipped scenario uses it"}}
    return {"status": 400, "body": {"error": f"unknown trigger kind {kind!r}"}}


# ─── system controller (bootstrap/teardown with live progress) ──────────────
# The runner executes one action at a time, captures line-buffered output, and
# parses the scripts' own markers into structured events. Scripts are unmodified.
_MARKERS = [
    ("phase",  re.compile(r"^── (?:\d+ · )?(.+?) ──$")),          # bootstrap steps
    ("banner", re.compile(r"^== (.+?) ==$")),                     # seed banners
    ("step",   re.compile(r"^▸ (.+)$")),                          # sub-steps/jobs
    ("ok",     re.compile(r"^\s{1,4}ok: (.+)$")),
    ("fail",   re.compile(r"^\s{1,4}FAIL: (.+)$")),
    ("warn",   re.compile(r"^\s{1,4}! (.+)$")),
]

# Exactly two actions, by design: rebuild the whole system from scratch, or tear
# it all down. Teardown preempts a running rebuild: it cancels the boot's whole
# process group first, then runs the (idempotent) teardown so the system returns
# to ground state and Rebuild can retry cleanly. Rebuild runs sources -> ingestors
# -> pipeline; the optional --fast flag restores the derived mart snapshot instead
# of running the pipeline.
ACTIONS = {
    "rebuild":  (["bash", str(SCRIPTS / "bootstrap.sh")],
                 "Full from-scratch spin-up: cluster -> images -> sources -> ingestors -> pipeline -> marts"),
    "teardown": (["bash", str(SCRIPTS / "bootstrap.sh"), "teardown"],
                 "Cancel any running rebuild, delete the cluster, stop the source replicas"),
}


class Runner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.action: str | None = None
        self.started: float | None = None
        self.rc: int | None = None
        self.events: list[dict] = []          # full history for the current action

    def cancel(self) -> bool:
        """Kill the running action's whole process group (the boot spawns
        docker/kubectl/k3d children, which a plain terminate would orphan)."""
        with self.lock:
            proc = self.proc
        if not (proc and proc.poll() is None):
            return False
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=10)
        with self.lock:
            self.events.append(self._ev("cancelled",
                                        f"{self.action} cancelled: preempted"))
        return True

    def start(self, action: str, extra_args: tuple[str, ...] = ()) -> tuple[bool, str]:
        argv, _desc = ACTIONS[action]
        argv = [*argv, *extra_args]
        # Teardown preempts: cancel whatever is running, then reset to ground
        # state. Any other action must wait for the current one.
        if action == "teardown":
            self.cancel()
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return False, f"action '{self.action}' is still running"
            self.action, self.started, self.rc = action, time.time(), None
            suffix = f" {' '.join(extra_args)}" if extra_args else ""
            self.events = [self._ev("start", f"{action}{suffix}: {ACTIONS[action][1]}")]
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            cmd = (["stdbuf", "-oL", "-eL"] if shutil.which("stdbuf") else []) + argv
            # start_new_session=True -> own process group, so cancel() can kill
            # the entire tree (bash -> kubectl/k3d/docker) in one signal.
            self.proc = subprocess.Popen(
                cmd, cwd=str(REPO), env=env, text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            threading.Thread(target=self._pump, daemon=True).start()
            return True, "started"

    def _ev(self, kind: str, text: str) -> dict:
        return {"t": round(time.time(), 2), "kind": kind, "text": text}

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            kind = "line"
            text = line
            for k, rx in _MARKERS:
                m = rx.match(line)
                if m:
                    kind, text = k, m.group(1)
                    break
            with self.lock:
                self.events.append(self._ev(kind, text))
        rc = self.proc.wait()
        with self.lock:
            self.rc = rc
            self.events.append(self._ev("done" if rc == 0 else "error",
                                        f"exit code {rc}"))

    def status(self) -> dict:
        with self.lock:
            running = bool(self.proc and self.proc.poll() is None)
            return {"action": self.action, "running": running, "rc": self.rc,
                    "started": self.started, "events": len(self.events)}

    def events_since(self, idx: int) -> tuple[list[dict], int]:
        with self.lock:
            return self.events[idx:], len(self.events)


RUNNER = Runner()


def _sh(cmd: list[str], timeout: int = 8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:                                              # noqa: BLE001
        return ""


def _system_status() -> dict:
    """Cheap live snapshot: cluster present, pod readiness, sources, dashboards."""
    clusters = _sh(["k3d", "cluster", "list", "-o", "json"])
    cluster_up = '"nhdw"' in clusters
    pods_total = pods_ready = 0
    if cluster_up:
        out = _sh(["kubectl", "-n", "nhdw", "get", "pods", "--no-headers"], 12)
        rows = [l for l in out.splitlines() if l.strip()]
        pods_total = len(rows)
        pods_ready = sum(1 for l in rows if ("Running" in l and "0/" not in l.split()[1])
                         or "Completed" in l)
    src = _sh(["docker", "ps", "--format", "{{.Names}}"])
    sources = {name: (name in src) for name in
               ["dhis2-web", "ethioemr-mysql", "hcmiswdm-sqlserver",
                "nhdw-mfr-fhir", "climate-api", "fayda-replica"]}
    return {
        "cluster_up": cluster_up,
        "pods": {"ready": pods_ready, "total": pods_total},
        "sources": sources,
        "runner": RUNNER.status(),
        "dashboards": {
            "Public":      "https://public.127.0.0.1.nip.io/",
            "Internal BI": "https://internal-bi.127.0.0.1.nip.io/",
            "Admin":       "https://admin.127.0.0.1.nip.io/",
            "Workshop":    "https://workshop.127.0.0.1.nip.io/",
        },
    }


# ─── HTTP layer ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers ------------------------------------------------------------
    def _json(self, obj: dict | list, status: int = 200) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path, ctype: str | None = None) -> None:
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        raw = path.read_bytes()
        ctypes = {".html": "text/html", ".js": "application/javascript",
                  ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
                  ".json": "application/json", ".ico": "image/x-icon",
                  ".woff2": "font/woff2", ".map": "application/json"}
        self.send_response(200)
        self.send_header("Content-Type", ctype or ctypes.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n)) if n else {}
        except Exception:                                          # noqa: BLE001
            return {}

    def log_message(self, fmt: str, *args) -> None:                # quieter logs
        if "/api/console/stream" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # -- GET ------------------------------------------------------------------
    def do_GET(self) -> None:                                       # noqa: N802
        path, _, query = self.path.partition("?")
        qs = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

        if path == "/health":
            return self._json({"status": "ok", "service": "nhdw-demo-console",
                               "auth": "anonymous"})
        if path == "/api/demo/pages":
            cat = _load_catalog()
            counts: dict[str, int] = {}
            for s in cat["scenarios"]:
                counts[s.get("page", "unknown")] = counts.get(s.get("page", "unknown"), 0) + 1
            return self._json({"pages": [{**p, "scenario_count": counts.get(p["id"], 0)}
                                         for p in cat["pages"]]})
        if path == "/api/demo/scenarios":
            cat = _load_catalog()
            items = cat["scenarios"]
            if "page" in qs:
                items = [s for s in items if s.get("page") == qs["page"]]
            if "category" in qs:
                items = [s for s in items if qs["category"] in
                         (s.get("categories") or [s.get("category", "")])]
            return self._json({
                "count": len(items), "scenarios": items,
                "categories": sorted({c for s in items
                                      for c in (s.get("categories") or [s.get("category", "")])
                                      if c})})
        m = re.fullmatch(r"/api/demo/scenarios/([^/]+)", path)
        if m:
            cat = _load_catalog()
            for s in cat["scenarios"]:
                if s.get("id") == m.group(1):
                    return self._json(s)
            return self._json({"error": f"scenario {m.group(1)} not found"}, 404)
        if path == "/api/demo/credentials":
            cat = _load_catalog()
            return self._json({"count": len(cat["credentials"]),
                               "credentials": cat["credentials"]})

        # console controller
        if path == "/api/console/status":
            return self._json(_system_status())
        if path == "/api/console/actions":
            return self._json({k: v[1] for k, v in ACTIONS.items()})
        if path == "/api/console/stream":
            return self._sse()

        # static: the SPA (its sidebar carries the System · Console page)
        if path in ("/console", "/console/"):   # old bookmark -> the SPA
            self.send_response(302); self.send_header("Location", "/"); self.end_headers(); return
        if path == "/" or path == "/index.html":
            return self._file(DIST_DIR / "index.html")
        if path.startswith("/assets/"):
            return self._file(DIST_DIR / path.lstrip("/"))
        if path.startswith("/static/"):
            return self._file(STATIC_DIR / path[len("/static/"):])
        # SPA has no client router; anything else is 404 via file check
        return self._file(DIST_DIR / path.lstrip("/"))

    # -- POST -----------------------------------------------------------------
    def do_POST(self) -> None:                                      # noqa: N802
        path = self.path.partition("?")[0]
        m = re.fullmatch(r"/api/demo/scenarios/([^/]+)/trigger", path)
        if m:
            cat = _load_catalog()
            scenario = next((s for s in cat["scenarios"] if s.get("id") == m.group(1)), None)
            if scenario is None:
                return self._json({"error": f"scenario {m.group(1)} not found"}, 404)
            trig = scenario.get("trigger") or {}
            if not trig.get("kind"):
                return self._json({"error": "scenario is instruction-only: no trigger bound"}, 400)
            args = {**(trig.get("args") or {}), **self._body()}
            result = _trigger(trig["kind"], args)
            return self._json({"scenario_id": m.group(1), "kind": trig["kind"],
                               "args": args, "result": result})
        if path == "/api/demo/reload":
            cat = _load_catalog(force=True)
            return self._json({"reloaded": True, "scenarios": len(cat["scenarios"]),
                               "credentials": len(cat["credentials"]),
                               "pages": len(cat["pages"])})
        if path == "/api/console/action":
            body = self._body()
            action = body.get("action", "")
            if action not in ACTIONS:
                return self._json({"error": f"unknown action {action!r}",
                                   "known": list(ACTIONS)}, 400)
            extra: tuple[str, ...] = ()
            if action == "teardown" and body.get("wipe_sources"):
                extra = ("--wipe-sources",)   # factory reset: sources re-seed from the bundle
            if action == "rebuild" and body.get("fast"):
                extra = ("--fast",)   # restore the derived mart snapshot + skip the pipeline
            ok, msg = RUNNER.start(action, extra)
            return self._json({"ok": ok, "message": msg,
                               "action": action}, 200 if ok else 409)
        return self._json({"error": "not found"}, 404)

    # -- SSE ------------------------------------------------------------------
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        idx = 0
        try:
            while True:
                events, idx = RUNNER.events_since(idx)
                for ev in events:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                st = RUNNER.status()
                if not st["running"] and idx >= st["events"] and idx > 0:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONSOLE_PORT", "8500")))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"NHDW Demo Console -> http://{args.host}:{args.port}/  (System · Console in the sidebar)")
    print(f"  gateway          -> {GATEWAY_URL}")
    print(f"  scenarios        -> {SCENARIOS_PATH}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
