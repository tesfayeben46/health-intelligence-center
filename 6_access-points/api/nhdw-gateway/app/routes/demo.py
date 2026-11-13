"""Demo-control contract.

Five endpoints that let an external console drive this deployment: health,
active country profile, profile switch, scripted actions, and job progress.
Other projects can implement the same five endpoints their own way; the console
does not depend on what is behind them, so adding a project is a configuration
entry rather than an integration.

    GET   /api/demo/health      up? degraded? which components
    GET   /api/demo/profile     active profile + what else is installed
    POST  /api/demo/profile     switch profile, returns a job id
    POST  /api/demo/action/{id} run a scripted action, returns a job id
    GET   /api/demo/job/{id}    stage-by-stage progress for either of those

Authorisation: every mutating route requires the demo-control service token
(`DEMO_CONTROL_TOKEN`), the same shared-secret pattern as the DQ detector. When
the token is not configured, the mutating routes are disabled rather than
falling back to a weaker check. `/health` stays open so the console can show
status before sign-in.

A profile has three layers with very different costs:

    presentation   copy, geography, branding   seconds, applied in-process
    appsec         people, roles, policy       about a minute, seed script
    data           source replicas, pipeline   a full pipeline run

Only `presentation` is applied in this process. The other layers need kubectl
and the seed scripts, so the request is handed to the host worker (see
swap_profile) and the job reports its progress until the worker finishes.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Body, Header, HTTPException
from pydantic import BaseModel, Field

from . import profile as profile_route
from ..settings import settings

log = logging.getLogger("gateway.demo")
router = APIRouter(prefix="/api/demo", tags=["demo"])

Layer = Literal["presentation", "appsec", "data"]

# Jobs are kept in memory: each describes one operation lasting seconds to
# minutes, and swap progress that must survive a restart comes from the host
# worker's status file. Durable history belongs in the console's activity log.
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_CAP = 200


def _new_job(kind: str, detail: str, stages: list[str]) -> dict[str, Any]:
    if len(_JOBS) >= _JOB_CAP:
        for key in sorted(_JOBS, key=lambda k: _JOBS[k]["started_at"])[: _JOB_CAP // 4]:
            _JOBS.pop(key, None)
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "detail": detail,
        "state": "running",
        "started_at": time.time(),
        "finished_at": None,
        "stages": [{"name": s, "state": "waiting", "note": None,
                    "started_at": None, "finished_at": None} for s in stages],
        "error": None,
    }
    _JOBS[job["id"]] = job
    return job


def _stage(job: dict[str, Any], name: str, state: str, note: str | None = None) -> None:
    for s in job["stages"]:
        if s["name"] == name:
            if state == "running" and s["started_at"] is None:
                s["started_at"] = time.time()
            if state in ("done", "failed", "skipped"):
                s["finished_at"] = time.time()
            s["state"] = state
            if note:
                s["note"] = note
            return


def _finish(job: dict[str, Any], state: str, error: str | None = None) -> None:
    job["state"] = state
    job["error"] = error
    job["finished_at"] = time.time()


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    return {
        **{k: v for k, v in job.items() if k != "stages"},
        "elapsed_s": round((job["finished_at"] or now) - job["started_at"], 1),
        "stages": [
            {**s, "elapsed_s": round(((s["finished_at"] or now) - s["started_at"]), 1)
                    if s["started_at"] else None}
            for s in job["stages"]
        ],
    }


# Auth

def _require_control(token: str | None) -> None:
    configured = getattr(settings, "DEMO_CONTROL_TOKEN", "") or ""
    if not configured:
        raise HTTPException(
            503, "demo control is disabled on this deployment "
                 "(no DEMO_CONTROL_TOKEN configured)")
    if token != configured:
        raise HTTPException(403, "bad demo-control token")


# Health

@router.get("/health")
async def health() -> dict[str, Any]:
    """Report whether this deployment is demo-ready.

    More than a ping: a gateway can answer while the warehouse is empty, so
    this reports the components a demo depends on and lets the console decide
    what to show."""
    components: list[dict[str, Any]] = []

    async def probe(name: str, coro, detail: str = "") -> None:
        started = time.perf_counter()
        try:
            ok = bool(await asyncio.wait_for(coro, timeout=5))
            components.append({"name": name, "ok": ok, "detail": detail,
                               "ms": int((time.perf_counter() - started) * 1000)})
        except Exception as exc:                                # noqa: BLE001
            components.append({"name": name, "ok": False, "detail": str(exc)[:120],
                               "ms": int((time.perf_counter() - started) * 1000)})

    async def ops_db_ok() -> bool:
        from .. import ops_db
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1

    async def marts_ok() -> bool:
        from .. import trino_client
        res = await trino_client.query(
            "SELECT count(*) AS n FROM marts.bi_quality.mart_data_quality")
        rows = res.get("rows") or []
        return bool(rows) and int(rows[0].get("n") or 0) > 0

    await probe("operations database", ops_db_ok())
    await probe("marts", marts_ok(), "the numbers every dashboard reads")

    profile_ok = True
    try:
        installed = profile_route.list_profiles()
    except Exception:                                           # noqa: BLE001
        installed, profile_ok = [], False
    components.append({"name": "country profiles", "ok": profile_ok and bool(installed),
                       "detail": f"{len(installed)} installed", "ms": 0})

    healthy = all(c["ok"] for c in components)
    return {
        "ok": healthy,
        "state": "live" if healthy else "degraded",
        "components": components,
        "active_profile": profile_route.active_key(),
        "checked_at": time.time(),
    }


# Profile

@router.get("/profile")
async def get_profile() -> dict[str, Any]:
    return {"active": profile_route.active_key(),
            "installed": profile_route.list_profiles()}


class SwapRequest(BaseModel):
    profile: str = Field(min_length=1, max_length=64)
    # Layers to apply. Presentation alone is the fast path ("show this as
    # another country"); including `data` means a pipeline run.
    layers: list[Layer] = Field(default_factory=lambda: ["presentation"])


@router.post("/profile")
async def swap_profile(body: SwapRequest = Body(...),
                       x_demo_token: str | None = Header(default=None,
                                                         alias="X-Demo-Token")) -> dict[str, Any]:
    """Switch the active profile.

    Returns a job immediately. The presentation layer is applied synchronously
    (a pointer write and a cache drop); the heavier layers are handed to the
    host worker and reported through the job, never silently skipped."""
    _require_control(x_demo_token)

    available = {p["key"] for p in profile_route.list_profiles()}
    if body.profile not in available:
        raise HTTPException(404, f"no profile '{body.profile}' installed on this host "
                                 f"(have: {', '.join(sorted(available)) or 'none'})")

    ordered = [l for l in ("presentation", "appsec", "data") if l in body.layers]
    if not ordered:
        raise HTTPException(400, "no layers requested")

    job = _new_job("profile-swap", f"{profile_route.active_key()} -> {body.profile}",
                   ordered + ["verify"])

    try:
        if "presentation" in ordered:
            _stage(job, "presentation", "running")
            profile_route.set_active(body.profile)
            _stage(job, "presentation", "done",
                   "copy, geography and branding now serve the new profile")

        heavy = [l for l in ("appsec", "data") if l in ordered]
        if heavy:
            # The gateway cannot run these (no cluster shell, no docker socket),
            # but it can write to its host mount. The request is written to
            # swap-requests/, the host worker runs the same scripts a cold boot
            # runs, and progress returns through swap-status/<job>.json, which
            # _job_view merges.
            _enqueue_swap(job["id"], body.profile, heavy)
            for l in heavy:
                _stage(job, l, "waiting", "queued for the host worker")
            _stage(job, "verify", "running")
            active = profile_route.active_key()
            if active != body.profile:
                raise RuntimeError(f"pointer says '{active}' after writing '{body.profile}'")
            _stage(job, "verify", "done",
                   f"presentation serves {active}; heavier layers run behind this job")
            # not finished here: the worker's status file completes it
        else:
            _stage(job, "verify", "running")
            active = profile_route.active_key()
            if active != body.profile:
                raise RuntimeError(f"pointer says '{active}' after writing '{body.profile}'")
            _stage(job, "verify", "done", f"serving {active}")
            _finish(job, "succeeded")
    except Exception as exc:                                    # noqa: BLE001
        log.exception("demo: profile swap failed")
        _finish(job, "failed", str(exc)[:300])

    return _job_view(job)


def _swap_dirs():
    from .profile import STATE_DIR
    req = STATE_DIR / "swap-requests"; st = STATE_DIR / "swap-status"
    req.mkdir(parents=True, exist_ok=True); st.mkdir(parents=True, exist_ok=True)
    return req, st


def _enqueue_swap(job_id: str, profile: str, layers: list[str]) -> None:
    import json as _json
    req, st = _swap_dirs()
    # One swap at a time: concurrent pipeline runs would interleave writes to
    # the same warehouse, so a second request is refused.
    for f in st.glob("*.json"):
        try:
            doc = _json.loads(f.read_text())
        except Exception:                                       # noqa: BLE001
            continue
        if doc.get("state") == "running":
            raise HTTPException(409, f"a profile swap is already running "
                                     f"(job {doc.get('job_id')}); wait for it")
    payload = {"job_id": job_id, "profile": profile,
               "layers": ",".join(layers), "requested_at": time.time()}
    tmp = req / f".{job_id}.tmp"
    tmp.write_text(_json.dumps(payload))
    tmp.rename(req / f"{job_id}.json")


_WORKER_STAGES = ("appsec", "keycloak-cast", "gateway-restart", "sources", "pipeline")


def _merge_worker_status(job_id: str, view: dict[str, Any]) -> dict[str, Any]:
    """Merge the host worker's status file into a job view. Works with or
    without an in-memory job: the worker restarts this gateway during the appsec
    stage, so the file is the durable record and memory is a cache."""
    import json as _json
    _, st = _swap_dirs()
    f = st / f"{job_id}.json"
    if not f.is_file():
        return view
    try:
        doc = _json.loads(f.read_text())
    except Exception:                                           # noqa: BLE001
        return view
    wstages = doc.get("stages") or {}
    have = {s["name"] for s in view.get("stages", [])}
    for name in _WORKER_STAGES:
        w = wstages.get(name)
        if not w:
            continue
        entry = {"name": name, "state": w.get("state"), "note": w.get("note"),
                 "started_at": None, "finished_at": None}
        if name in have:
            view["stages"] = [entry if s["name"] == name else s for s in view["stages"]]
        else:  # insert before the trailing verify stage
            idx = next((i for i, s in enumerate(view["stages"]) if s["name"] == "verify"),
                       len(view["stages"]))
            view["stages"].insert(idx, entry)
            have.add(name)
    if doc.get("state") in ("succeeded", "failed"):
        view["state"] = doc["state"]
        view["note"] = doc.get("note") or view.get("note")
        view["finished_at"] = doc.get("updated_at")
    return view


_HEAVY_NOTE = {
    "appsec": "not applied here; people, roles and policy are reseeded by "
              "seed-app-security.sh, which needs cluster access the gateway does not have",
    "data": "not applied here; swapping source replicas means re-running the "
            "pipeline end to end (sources-up.sh then seed-realistic.sh); it is a "
            "scheduled operation, not a request/response",
}


# Actions. A demo action is a named operation, reversible where possible, that a
# presenter triggers from the console, with a deep link to the page that shows
# its effect. Actions are declared rather than free-form so the console can list
# them and the effect of each is documented.

ACTIONS: list[dict[str, Any]] = [
    {
        "id": "reset-profile",
        "label": "Reset to the default profile",
        "description": "Switches presentation back to the profile this deployment ships with.",
        "deep_link": "/",
        "reversible": True,
        "blast_radius": "presentation only; no data is written",
    },
    {
        "id": "refresh-dq",
        "label": "Re-run quality detection",
        "description": "Runs the data-quality detector so the incident queue reflects "
                       "the warehouse as it stands right now.",
        "deep_link": "/admin/quality",
        "reversible": True,
        "blast_radius": "writes incident rows; deduped by fingerprint, so a re-run "
                        "updates rather than accumulates",
    },
]


@router.get("/actions")
async def list_actions() -> dict[str, Any]:
    return {"actions": ACTIONS}


@router.post("/action/{action_id}")
async def run_action(action_id: str,
                     x_demo_token: str | None = Header(default=None,
                                                       alias="X-Demo-Token")) -> dict[str, Any]:
    _require_control(x_demo_token)
    action = next((a for a in ACTIONS if a["id"] == action_id), None)
    if action is None:
        raise HTTPException(404, f"no demo action '{action_id}'")

    job = _new_job("demo-action", action["label"], ["run"])
    _stage(job, "run", "running")
    try:
        if action_id == "reset-profile":
            import os as _os
            default = _os.environ.get("NHDW_PROFILE", "et-v3")
            profile_route.set_active(default)
            _stage(job, "run", "done", f"serving {default}")
        elif action_id == "refresh-dq":
            from . import dq
            summary = await dq._run_detection()
            _stage(job, "run", "done",
                   f"{summary.get('findings', 0)} findings, "
                   f"{summary.get('open_flags', 0)} open")
        _finish(job, "succeeded")
    except Exception as exc:                                    # noqa: BLE001
        log.exception("demo: action %s failed", action_id)
        _stage(job, "run", "failed", str(exc)[:200])
        _finish(job, "failed", str(exc)[:300])

    return {**_job_view(job), "deep_link": action["deep_link"]}


# Jobs

@router.get("/job/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = _JOBS.get(job_id)
    if job is not None:
        return _merge_worker_status(job_id, _job_view(job))
    # Not in memory: the worker restarts this gateway mid-swap while the
    # console keeps polling the same id. Rebuild the view from the status
    # file alone.
    skeleton = {"id": job_id, "kind": "profile-swap", "detail": "",
                "state": "running", "note": "gateway restarted during the swap; "
                "progress reconstructed from the host worker", "stages": [
                    {"name": "verify", "state": "done", "note": None,
                     "started_at": None, "finished_at": None}]}
    merged = _merge_worker_status(job_id, skeleton)
    if merged is skeleton and len(skeleton["stages"]) == 1 and \
            skeleton["stages"][0]["name"] == "verify" and not (
            _swap_dirs()[1] / f"{job_id}.json").is_file():
        raise HTTPException(404, "no such job (jobs are kept in memory and do not "
                                 "survive a gateway restart)")
    return merged
