"""/api/graduation: project graduation (public review).

The unit of review is the project: an owner submits a project to become
public. Review has two parts:
  (a) an automated essential-files check over the project's files (an inference
      script, a retrain script if a model is present, a model artifact or
      notebook, metadata or a readme), and
  (b) a human reviewer who opens the project (spawns a lab, see workspace.py),
      runs its notebooks and approves.
On approval the project becomes public and its model is integrated into the
catalogue by ml_integration.integrate_approved_project_model.

Flow:
   POST /api/graduation/project/{project_id}/submit   (contributor + owner)
     -> runs the essential-files check -> review_checklist
     -> project.review_status = 'in_review' (plus submitted_at, submitted_by)
     -> returns the checklist and all_essentials_present (submission is allowed
       with gaps; the reviewer decides, and the flag lets the UI warn)

   GET  /api/graduation/project-queue                 (reviewers only)
     -> projects WHERE review_status IN ('in_review','changes_requested')

   GET  /api/graduation/project/{project_id}          (reviewer or owner)
     -> full detail including checklist and file listing

   POST /api/graduation/project/{project_id}/approve  (reviewers only)
     -> review_status='approved', reviewed_at/by, visibility='public'
     -> project_review verdict='approved'
     -> then integrate_approved_project_model(...) (best-effort)

   POST /api/graduation/project/{project_id}/reject   (reviewers only)
     -> {note, changes_requested?} -> 'changes_requested' | 'rejected'
     -> project_review verdict

POST /api/graduation/project/{project_id}/adopt-model  (reviewers only)
     -> bind an already-integrated platform model to its project (adopt_model)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path as PathParam

from ..auth import UserCtx, get_user
from ..settings import settings
from .. import ops_db, opa, minio_client, ml_integration

log = logging.getLogger("gateway.graduation")
router = APIRouter(prefix="/api/graduation", tags=["graduation"])

MLFLOW_URL      = os.environ.get("MLFLOW_URL",
                                 "http://mlflow.nhdw.svc.cluster.local:5000")
# Project files live in the bucket and prefix projects.py writes to; duplicated
# here so the two routers stay independent.
PROJECTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", settings.ARTIFACTS_BUCKET)


def _files_prefix(pid: str) -> str:
    return f"projects/{pid}/files/"


# Authorisation: a direct role gate, as in ml.py `_manage_gate`. The graduation
# verbs have no OPA Rego rule (the data-plane policies cover only DC.* read and
# write). REVIEWER_ROLES and CONTRIBUTOR_ROLES are imported by routes/groups.py
# and routes/datasets.py.
REVIEWER_ROLES    = ("R-OPS-ADM", "R-STEW", "R-SUPER")
CONTRIBUTOR_ROLES = ("R-CONS-SCI", "R-CONS-RES", "R-CONS-ANA",
                     "R-CONS-PRT") + REVIEWER_ROLES


def is_reviewer(user: UserCtx) -> bool:
    return any(r in REVIEWER_ROLES for r in user.roles)


def _review_gate(user: UserCtx) -> opa.Decision:
    """Queue, approve and reject are reviewer actions. Checks the full role set:
    primary_role is only the first R- role in an unordered Keycloak claim, so a
    steward whose token carries e.g. ['R-CONS-ANA','R-STEW'] must not be denied."""
    allowed = is_reviewer(user)
    return opa.Decision(
        allow=allowed,
        reason=f"graduation review gate: roles {user.roles} "
               f"{'allowed' if allowed else 'denied'} (reviewers only)")


def _contrib_gate(user: UserCtx) -> opa.Decision:
    """Submit is a contributor action (any workshop contributor, including
    reviewers who are also contributors). Full role-set check (see _review_gate)."""
    allowed = any(r in CONTRIBUTOR_ROLES for r in user.roles)
    return opa.Decision(
        allow=allowed,
        reason=f"graduation contributor gate: roles {user.roles} "
               f"{'allowed' if allowed else 'denied'} (contributors only)")


# Essential-files check.
# Heuristic, case-insensitive match on each file's basename (the last path
# segment, so nested folders are fine). Requirements:
#   inference script            infer*.py | predict*.py           (required)
#   model artifact OR notebook  *.pkl|*.joblib|*.onnx|*.pt | *.ipynb (required)
#   retrain/train script        train*.py     (required only if a model artifact is present)
#   metadata/readme             README* | model_card* | manifest.y*ml (required)
_MODEL_EXTS   = (".pkl", ".joblib", ".onnx", ".pt")
_RE_INFER     = re.compile(r"^(infer|predict).*\.py$", re.I)
_RE_TRAIN     = re.compile(r"^train.*\.py$", re.I)
_RE_MANIFEST  = re.compile(r"^manifest\.ya?ml$", re.I)


def _basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _is_model(bn: str) -> bool:
    return bn.lower().endswith(_MODEL_EXTS)


def _is_notebook(bn: str) -> bool:
    return bn.lower().endswith(".ipynb")


def _is_meta(bn: str) -> bool:
    low = bn.lower()
    return (low.startswith("readme")
            or low.startswith("model_card")
            or bool(_RE_MANIFEST.match(bn)))


def essential_files_check(file_names: list[str]) -> list[dict]:
    """Build the checklist from a list of project-relative file names. Each item
    is {requirement, present, matched:[...], required}. `required` encodes the
    'retrain only if a model is present' rule so all_essentials_present can be
    derived consistently by both the gateway and the UI."""
    names = [n for n in file_names
             if n and n != ".keep" and not n.endswith("/.keep")]

    infer_m    = sorted(n for n in names if _RE_INFER.match(_basename(n)))
    train_m    = sorted(n for n in names if _RE_TRAIN.match(_basename(n)))
    model_m    = sorted(n for n in names if _is_model(_basename(n)))
    notebook_m = sorted(n for n in names if _is_notebook(_basename(n)))
    meta_m     = sorted(n for n in names if _is_meta(_basename(n)))
    has_model  = bool(model_m)

    return [
        {"requirement": "inference script (infer*/predict*.py)",
         "present": bool(infer_m), "matched": infer_m, "required": True},
        {"requirement": "model artifact (.pkl/.joblib/.onnx/.pt) or notebook (.ipynb)",
         "present": bool(model_m or notebook_m),
         "matched": model_m + notebook_m, "required": True},
        {"requirement": "retrain/train script (train*.py)",
         "present": bool(train_m), "matched": train_m,
         # required only when a model artifact is present in the project
         "required": has_model},
        {"requirement": "metadata/readme (README*/model_card*/manifest.y*ml)",
         "present": bool(meta_m), "matched": meta_m, "required": True},
    ]


def _all_essentials_present(checklist) -> bool:
    # jsonb decodes to a Python list via the ops_db codec, but a row may hold
    # a double-encoded JSON string; parse it rather than fail the whole queue.
    if isinstance(checklist, str):
        try:
            checklist = json.loads(checklist)
        except Exception:                                         # noqa: BLE001
            return False
    if not checklist or not isinstance(checklist, list):
        return False
    return all(item.get("present") for item in checklist
               if isinstance(item, dict) and item.get("required"))


async def _list_project_files(project_id: str) -> list[dict]:
    """Raw project file listing (name, size, last_modified), markers removed."""
    objs = await minio_client.list_prefix(prefix=_files_prefix(project_id),
                                           bucket=PROJECTS_BUCKET)
    out = []
    for o in objs:
        name = o["name"]
        if not name or name == ".keep" or name.endswith("/.keep"):
            continue
        lm = o.get("last_modified")
        out.append({"name": name, "size": o.get("size", 0),
                    "last_modified": lm.isoformat() if hasattr(lm, "isoformat") else lm})
    return out


async def _model_manifest_fingerprint(project_id: str,
                                      model_name: str) -> tuple[str, str, int]:
    """Locate the adopted model's manifest object under the project's files/
    prefix and return (minio_key, sha256, byte_count) for its artifact version.
    The shallowest key whose basename is manifest.yml or manifest.yaml wins (the
    package's top-level manifest). If none is present or readable, returns the
    files/ prefix, a hash of the model name and byte_count 0, so a project
    without uploaded files can still be adopted."""
    prefix = _files_prefix(project_id)
    objs = await minio_client.list_prefix(prefix=prefix, bucket=PROJECTS_BUCKET)
    cand = sorted((o["key"] for o in objs
                   if _RE_MANIFEST.match(_basename(o["key"]))),
                  key=lambda k: (k.count("/"), k))
    if cand:
        raw = await minio_client.read_object(key=cand[0], bucket=PROJECTS_BUCKET)
        if raw is not None:
            return cand[0], hashlib.sha256(raw).hexdigest(), len(raw)
    return prefix, hashlib.sha256(model_name.encode()).hexdigest(), 0


def _proj_out(row) -> dict:
    """JSON-safe project row (uuid as str, timestamps as ISO format)."""
    d = dict(row)
    for k in ("id", "owner_kc_sub", "group_id", "submitted_by", "reviewed_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("created_at", "updated_at", "submitted_at", "reviewed_at"):
        v = d.get(k)
        if v is not None and hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


# Submit (contributor who owns the project or is a member of its group)
@router.post("/project/{project_id}/submit")
async def submit_project(
    project_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
) -> dict:
    decision = _contrib_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied")
    try:
        UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "project not found")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        prj = await conn.fetchrow("""
            SELECT owner_kc_sub, visibility, group_id, status, review_status
            FROM ops_workspace.project WHERE id = $1::uuid;
        """, project_id)
        if prj is None:
            raise HTTPException(404, "project not found")
        if prj["status"] != "active":
            raise HTTPException(409, "project is archived")
        # Submit requires being the owner, or a member of a group project's group
        # (as projects._assert_contribute).
        if str(prj["owner_kc_sub"]) != (user.kc_sub or ""):
            is_member = prj["visibility"] == "group" and bool(
                await conn.fetchval("""
                    SELECT 1 FROM ops_artifact.workshop_group_member
                    WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
                """, prj["group_id"], user.kc_sub))
            if not is_member:
                raise HTTPException(
                    403, "you cannot submit this project "
                         "(owner or project-group members only)")
        if prj["review_status"] == "in_review":
            raise HTTPException(409, "project is already under review")
        if prj["review_status"] == "approved":
            raise HTTPException(409, "project is already approved (public)")

    # Essential-files check over the project's files (I/O work, done outside
    # the DB transaction). Submission is allowed with gaps.
    files = await _list_project_files(project_id)
    checklist = essential_files_check([f["name"] for f in files])
    all_present = _all_essentials_present(checklist)

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE ops_workspace.project
               SET review_status = 'in_review',
                   submitted_at = now(),
                   submitted_by = $2::uuid,
                   review_checklist = $3::jsonb,
                   reviewed_at = NULL, reviewed_by = NULL,
                   updated_at = now()
             WHERE id = $1::uuid
        """, project_id, user.kc_sub, checklist)

    await ops_db.log_data_event(
        user=user, kind="graduation.project_submitted",
        payload={"project_id": project_id,
                 "all_essentials_present": all_present,
                 "checklist": [{"r": c["requirement"], "p": c["present"],
                                "req": c["required"]} for c in checklist]})
    return {"project_id": project_id, "review_status": "in_review",
            "checklist": checklist, "all_essentials_present": all_present}


# Reviewer queue
@router.get("/project-queue")
async def project_queue(user: UserCtx = Depends(get_user)) -> dict:
    decision = _review_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "reviewers only")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.id, p.slug, p.name, p.visibility, p.review_status,
                   p.owner_kc_sub, u.username AS owner_username,
                   p.submitted_at, p.submitted_by, p.review_checklist
            FROM ops_workspace.project p
            LEFT JOIN ops_identity."user" u ON u.kc_sub = p.owner_kc_sub
            WHERE p.review_status IN ('in_review','changes_requested')
            ORDER BY p.submitted_at DESC NULLS LAST LIMIT 200;
        """)
    queue = []
    for r in rows:
        d = _proj_out(r)
        d["all_essentials_present"] = _all_essentials_present(r["review_checklist"])
        queue.append(d)
    return {"queue": queue, "count": len(queue)}


# Detail (reviewer or owner)
@router.get("/project/{project_id}")
async def project_detail(project_id: str = PathParam(...),
                         user: UserCtx = Depends(get_user)) -> dict:
    try:
        UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        prj = await conn.fetchrow("""
            SELECT p.*, u.username AS owner_username
            FROM ops_workspace.project p
            LEFT JOIN ops_identity."user" u ON u.kc_sub = p.owner_kc_sub
            WHERE p.id = $1::uuid;
        """, project_id)
        if prj is None:
            raise HTTPException(404, "not found")
        is_owner = bool(user.kc_sub) and str(prj["owner_kc_sub"]) == user.kc_sub
        # A reviewer may see the project only once it has been submitted
        # (in_review, changes_requested, approved), never a draft; a draft
        # returns 404, not 403, to prevent enumeration.
        reviewer_ok = is_reviewer(user) and prj["review_status"] in (
            "in_review", "changes_requested", "approved")
        if not (is_owner or reviewer_ok):
            raise HTTPException(404, "not found")
        reviews = [dict(r) for r in await conn.fetch("""
            SELECT id, reviewer, verdict, comment, reviewed_at
            FROM ops_workspace.project_review
            WHERE project_id = $1::uuid ORDER BY reviewed_at DESC LIMIT 50;
        """, project_id)]

    files = await _list_project_files(project_id)
    # Recompute the checklist over the current files; the stored one is the
    # snapshot at submission.
    checklist = essential_files_check([f["name"] for f in files])
    out = _proj_out(prj)
    for rv in reviews:
        rv["id"] = str(rv["id"]); rv["reviewer"] = str(rv["reviewer"])
        if hasattr(rv["reviewed_at"], "isoformat"):
            rv["reviewed_at"] = rv["reviewed_at"].isoformat()
    return {"project": out,
            "checklist": checklist,
            "all_essentials_present": _all_essentials_present(checklist),
            "files": files, "file_count": len(files),
            "reviews": reviews}


# Approve (reviewers only)
@router.post("/project/{project_id}/approve")
async def approve_project(
    project_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    comment: str | None = Body(default=None, embed=True),
) -> dict:
    decision = _review_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "reviewers only")
    try:
        UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        prj = await conn.fetchrow("""
            SELECT review_status, review_checklist FROM ops_workspace.project
            WHERE id = $1::uuid;
        """, project_id)
        if prj is None:
            raise HTTPException(404, "not found")
        if prj["review_status"] not in ("in_review", "changes_requested"):
            raise HTTPException(
                409, f"project is not awaiting review "
                     f"(review_status={prj['review_status']})")
        checklist_obj = prj["review_checklist"]
        # All writes commit together, so a failure never leaves the project
        # public without a verdict record.
        async with conn.transaction():
            # Approval publishes the project (visibility='public'). The DB CHECK
            # forbids a group_id on non-group visibility, so it is cleared (a
            # group project graduating to public loses its group link).
            await conn.execute("""
                UPDATE ops_workspace.project
                   SET review_status = 'approved',
                       visibility = 'public', group_id = NULL,
                       reviewed_at = now(), reviewed_by = $2::uuid,
                       updated_at = now()
                 WHERE id = $1::uuid
            """, project_id, user.kc_sub)
            await conn.execute("""
                INSERT INTO ops_workspace.project_review
                    (project_id, reviewer, verdict, comment, checklist)
                VALUES ($1::uuid, $2::uuid, 'approved', $3, $4::jsonb)
            """, project_id, user.kc_sub, comment, checklist_obj)

        # Integrate the approved project's model into the catalogue. Best-effort:
        # approval must not fail on it, since the project is already public and
        # the verdict recorded.
        try:
            integration = await ml_integration.integrate_approved_project_model(
                conn, project_id, user)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("ml_integration hook failed for %s: %s", project_id, exc)
            integration = {"integrated": False, "reason": f"hook error: {exc}"[:300]}

    await ops_db.log_policy_decision(
        user=user, action="approve.project", resource=project_id,
        decision="allow", rule_hits=decision.rule_hits,
        explanation={"review_status": "approved", "visibility": "public",
                     "integration": integration})
    return {"project_id": project_id, "review_status": "approved",
            "visibility": "public", "integration": integration}


# Adopt a platform model onto its project (reviewers only)
@router.post("/project/{project_id}/adopt-model")
async def adopt_model(
    project_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    model_name: str = Body(..., embed=True),
    title: str | None = Body(default=None, embed=True),
    description: str | None = Body(default=None, embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """Bind an already-integrated platform model (trained by seed-ml.sh into
    MLflow and ops_ml.model_deployment) to a workshop project as an approved,
    public project: a kind='model' artifact bound to the MLflow model, an
    approved review record, and the project published. It does not retrain,
    fork or start a runner; it only makes the project the catalogue home of the
    model. Idempotent: re-running reuses the existing artifact and review row
    and re-asserts the approved, public state. Platform models bypass the
    submit-review gate, so any current review_status (including draft) is
    adopted."""
    decision = _review_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "reviewers only")
    try:
        UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # The model must be an integrated platform model with a deployment row
        # (registered by seed-ml.sh); a name that is not served is refused.
        if not await conn.fetchval(
                "SELECT 1 FROM ops_ml.model_deployment WHERE model_name = $1",
                model_name):
            raise HTTPException(404, f"model not registered: {model_name}")
        prj = await conn.fetchrow("""
            SELECT owner_kc_sub, slug, review_status
            FROM ops_workspace.project WHERE id = $1::uuid;
        """, project_id)
        if prj is None:
            raise HTTPException(404, "not found")
        owner = prj["owner_kc_sub"]

        # Fingerprint the model's manifest object for the artifact version's size
        # and hash (object-store I/O, outside the write path like approve's
        # essential-files check), with a fallback when no manifest is present.
        minio_key, sha256, byte_count = await _model_manifest_fingerprint(
            project_id, model_name)

        # All writes commit together (as in approve_project), so a failure never
        # leaves a public project without an artifact or verdict record.
        async with conn.transaction():
            # Reuse the model artifact already bound to this (project, MLflow
            # model) pair rather than creating a duplicate.
            aid = await conn.fetchval("""
                SELECT id FROM ops_artifact.artifact
                WHERE project_id = $1::uuid AND kind = 'model'
                  AND mlflow_model_name = $2;
            """, project_id, model_name)
            if aid is None:
                aid = await conn.fetchval("""
                    INSERT INTO ops_artifact.artifact
                        (owner, kind, title, description, license, tier,
                         project_id, mlflow_model_name, peer_review_status, tags)
                    VALUES ($1, 'model', $2, $3, 'CC-BY-4.0', 'public',
                            $4::uuid, $5, 'approved', $6::text[])
                    RETURNING id;
                """, owner, title or model_name, description, project_id,
                     model_name, ["model", "platform"])
                await conn.execute("""
                    INSERT INTO ops_artifact.artifact_version
                        (artifact_id, version, minio_bucket, minio_key,
                         sha256, byte_count, change_note)
                    VALUES ($1, 1, $2, $3, $4, $5,
                            'platform model adopted onto project');
                """, aid, PROJECTS_BUCKET, minio_key, sha256, byte_count)

            # Approve and publish. Platform models bypass the submit-review gate,
            # so any current review_status becomes approved. The DB CHECK
            # forbids a group_id on non-group visibility, so it is cleared.
            await conn.execute("""
                UPDATE ops_workspace.project
                   SET review_status = 'approved',
                       visibility = 'public', group_id = NULL,
                       reviewed_at = now(), reviewed_by = $2::uuid,
                       updated_at = now()
                 WHERE id = $1::uuid
            """, project_id, user.kc_sub)

            # One approved review row, not one per re-run.
            if not await conn.fetchval("""
                    SELECT 1 FROM ops_workspace.project_review
                    WHERE project_id = $1::uuid AND verdict = 'approved';
            """, project_id):
                await conn.execute("""
                    INSERT INTO ops_workspace.project_review
                        (project_id, reviewer, verdict, comment, checklist)
                    VALUES ($1::uuid, $2::uuid, 'approved', $3, NULL)
                """, project_id, user.kc_sub, note)

    await ops_db.log_policy_decision(
        user=user, action="adopt.model", resource=project_id,
        decision="allow", rule_hits=decision.rule_hits,
        explanation={"model_name": model_name, "artifact_id": str(aid),
                     "review_status": "approved", "adopted": True})
    return {"project_id": project_id, "model_name": model_name,
            "artifact_id": str(aid), "review_status": "approved",
            "adopted": True}


# Reject or request changes (reviewers only)
@router.post("/project/{project_id}/reject")
async def reject_project(
    project_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    note: str | None = Body(default=None, embed=True),
    changes_requested: bool = Body(default=False, embed=True),
) -> dict:
    decision = _review_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "reviewers only")
    try:
        UUID(project_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")

    new_status = "changes_requested" if changes_requested else "rejected"
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        prj = await conn.fetchrow("""
            SELECT review_status, review_checklist FROM ops_workspace.project
            WHERE id = $1::uuid;
        """, project_id)
        if prj is None:
            raise HTTPException(404, "not found")
        if prj["review_status"] not in ("in_review", "changes_requested"):
            raise HTTPException(
                409, f"project is not awaiting review "
                     f"(review_status={prj['review_status']})")
        checklist_obj = prj["review_checklist"]
        async with conn.transaction():
            await conn.execute("""
                UPDATE ops_workspace.project
                   SET review_status = $2,
                       reviewed_at = now(), reviewed_by = $3::uuid,
                       updated_at = now()
                 WHERE id = $1::uuid
            """, project_id, new_status, user.kc_sub)
            await conn.execute("""
                INSERT INTO ops_workspace.project_review
                    (project_id, reviewer, verdict, comment, checklist)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
            """, project_id, user.kc_sub, new_status, note, checklist_obj)

    await ops_db.log_data_event(
        user=user, kind="graduation.project_rejected",
        payload={"project_id": project_id, "review_status": new_status,
                 "note": note})
    return {"project_id": project_id, "review_status": new_status}


# MLflow alias helper
async def _set_mlflow_alias(canonical_name: str, alias: str) -> dict:
    """Point the MLflow alias at the latest registered version of the canonical
    model. Not called by the project flow (the integration runner sets
    @champion itself). Raises on failure; the caller decides how to report it."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{MLFLOW_URL}/api/2.0/mlflow/model-versions/search",
            params={"filter": f"name='{canonical_name}'", "max_results": "200"})
        r.raise_for_status()
        versions = (r.json() or {}).get("model_versions") or []
        if not versions:
            raise RuntimeError(
                f"no MLflow versions registered under {canonical_name!r}; "
                f"run the runner train Job for the package first")
        latest = max(versions, key=lambda v: int(v.get("version") or 0))
        r = await c.post(
            f"{MLFLOW_URL}/api/2.0/mlflow/registered-models/alias",
            json={"name": canonical_name, "alias": alias,
                  "version": latest["version"]})
        r.raise_for_status()
    return {"alias": alias, "version": latest["version"]}
