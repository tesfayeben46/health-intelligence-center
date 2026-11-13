/**
 * Workshop research-projects API client: backs the Projects catalogue and
 * project-detail view (ws-project-* widgets in the adoption map).
 *
 * Backend lives at /api/projects in the gateway (routes/projects.py, built in
 * parallel to this client); workshop's /gw/{path} proxy strips the `api/`
 * prefix, so client paths begin /gw/projects.
 *
 * Source of truth: ops_workspace.project (id/slug/name/owner_kc_sub/
 * visibility/group_id/status/description/timestamps). Creating a project is
 * one atomic server-side step: a project row, a projects/<id>/files/ prefix in
 * the object store and an MLflow experiment /projects/<id>; the client only
 * ever sees the resulting row.
 *
 * Visibility is the one tag that decides who sees a project:
 * private to owner only, group to members of group_id, public to everyone. The
 * gateway enforces it on every op; the client never sees another user's
 * private project.
 *
 * Coding to the CONTRACT, not the backend's uncommitted code: the list/detail
 * envelope isn't pinned by the contract, so the unwrap helpers below tolerate
 * either a bare array / row or a `{ projects | project | files }` wrapper:
 * they accept whichever shape the gateway returns.
 * Display strings (sizes, dates) are formatted CLIENT-SIDE from the structured
 * values the gateway returns: never pre-humanized.
 */
import { authFetch } from './useAuth';

const GW = '/gw/projects';

export type ProjectVisibility = 'private' | 'group' | 'public';
export type ProjectStatus = 'active' | 'archived';
export type ProjectScope = 'mine' | 'group' | 'public' | 'review' | 'all';
/** Public-review lifecycle on ops_workspace.project. */
export type ReviewStatus =
  | 'draft' | 'in_review' | 'changes_requested' | 'approved' | 'rejected' | string;

/** One essential-files checklist item returned by the submit / review APIs. */
export type ChecklistItem = {
  requirement: string;
  present: boolean;
  matched: string[];
  required: boolean;
};

/** One ops_workspace.project row as returned by the gateway. The core columns
 *  are always present; the enrichment fields are folded in by the gateway's
 *  joins (owner username, group name, contributor/artifact counts) when
 *  available and are rendered only when present. */
export type ProjectRow = {
  id: string;
  slug: string;
  name: string;
  owner_kc_sub: string;
  visibility: ProjectVisibility | string;
  group_id: string | null;
  status: ProjectStatus | string;
  description: string | null;
  created_at: string;
  updated_at: string;
  // optional gateway enrichments:
  owner_username?: string | null;
  group_name?: string | null;
  contributor_count?: number | null;
  artifact_count?: number | null;
  // public-review fields returned by the projects API:
  review_status?: ReviewStatus | null;
  submitted_at?: string | null;
  submitted_by?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  review_checklist?: ChecklistItem[] | null;
};

/** POST /gw/graduation/project/{id}/submit result. */
export type SubmitResult = {
  project_id: string;
  review_status: ReviewStatus;
  checklist: ChecklistItem[];
  all_essentials_present: boolean;
};

/** One object under the projects/<id>/files/ object-store prefix, listed through the
 *  gateway (no S3 credentials ever reach the browser). */
export type ProjectFile = {
  name: string;
  size: number;
  last_modified: string;
};

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

/** Accept `{ <key>: [...] }`, `{ items: [...] }`, or a bare array. */
function unwrapList<T>(j: unknown, key: string): T[] {
  if (Array.isArray(j)) return j as T[];
  if (j && typeof j === 'object') {
    const o = j as Record<string, unknown>;
    if (Array.isArray(o[key])) return o[key] as T[];
    if (Array.isArray(o.items)) return o.items as T[];
  }
  return [];
}

/** Accept `{ project: {...} }` or a bare row. */
function unwrapRow(j: unknown): ProjectRow {
  if (j && typeof j === 'object' && 'project' in (j as object)) {
    return (j as { project: ProjectRow }).project;
  }
  return j as ProjectRow;
}

/** GET /gw/projects: projects VISIBLE to the caller (own ∪ public ∪ groups
 *  the caller belongs to). `scope` (mine|group|public|all) + `q` name search
 *  are honoured server-side; the catalogue page also searches client-side over
 *  the returned payload. */
export async function listProjects(
  params: { scope?: ProjectScope; q?: string } = {},
): Promise<ProjectRow[]> {
  const qs = new URLSearchParams();
  if (params.scope) qs.set('scope', params.scope);
  if (params.q && params.q.trim()) qs.set('q', params.q.trim());
  const suffix = qs.toString() ? `?${qs}` : '';
  return unwrapList<ProjectRow>(await _json(`${GW}${suffix}`), 'projects');
}

/** GET /gw/projects/{id}: ACL-checked detail (404 if not visible). */
export async function getProject(id: string): Promise<ProjectRow> {
  return unwrapRow(await _json(`${GW}/${encodeURIComponent(id)}`));
}

/** POST /gw/projects: creates the B2 triple and returns the new row (201).
 *  group visibility can't be set here (the create route takes no group_id):
 *  create private/public, then share to a group from the detail visibility
 *  control (PATCH), which is where the group picker lives. */
export async function createProject(body: {
  name: string;
  description?: string;
  visibility?: ProjectVisibility;
}): Promise<ProjectRow> {
  return unwrapRow(await _json(GW, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

/** PATCH /gw/projects/{id}: owner-only. group_id is required iff
 *  visibility='group'; pass it only in that case. */
export async function updateProject(
  id: string,
  patch: {
    visibility?: ProjectVisibility;
    group_id?: string | null;
    name?: string;
    description?: string;
    status?: ProjectStatus;
  },
): Promise<ProjectRow> {
  return unwrapRow(await _json(`${GW}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch),
  }));
}

/** GET /gw/projects/{id}/files: gateway-proxied listing of the
 *  projects/<id>/files/ prefix. */
export async function listProjectFiles(id: string): Promise<ProjectFile[]> {
  return unwrapList<ProjectFile>(
    await _json(`${GW}/${encodeURIComponent(id)}/files`), 'files');
}

/** POST /gw/graduation/project/{id}/submit: submit a project to become public.
 *  Runs the essential-files check and returns the checklist; submission is
 *  ALLOWED even if essentials are missing (the human reviewer decides). Errors
 *  surface verbatim (403 not owner/contributor, 404, 409 already under review). */
export async function submitProjectForReview(id: string): Promise<SubmitResult> {
  return _json<SubmitResult>(
    `/gw/graduation/project/${encodeURIComponent(id)}/submit`,
    { method: 'POST', headers: { 'content-type': 'application/json' } });
}

/* presentation helpers (real visibility/status vocabulary only) */

/** review_status to human label (real vocabulary only). */
export function reviewStatusLabel(s: string | null | undefined): string {
  return ({
    draft: 'Draft', in_review: 'In review',
    changes_requested: 'Changes requested',
    approved: 'Public', rejected: 'Rejected',
  } as Record<string, string>)[s || 'draft'] || String(s);
}
/** review_status to pill class. approved reads as published (good). */
export function reviewStatusPillClass(s: string | null | undefined): string {
  return s === 'approved' ? 'good'
       : s === 'in_review' ? 'info'
       : s === 'changes_requested' ? 'watch'
       : s === 'rejected' ? 'alert'
       : 'neutral';
}

export function visibilityLabel(v: string): string {
  return ({ private: 'Private', group: 'Group', public: 'Public' } as
    Record<string, string>)[v] || v;
}
export function visibilityIcon(v: string): string {
  return ({ private: 'i-lock', group: 'i-users', public: 'i-globe' } as
    Record<string, string>)[v] || 'i-lock';
}
/** Pill class for a visibility badge: public reads as "open" (good),
 *  group as shared (info), private as neutral. */
export function visibilityPillClass(v: string): string {
  return v === 'public' ? 'good'
       : v === 'group' ? 'info'
       : 'neutral';
}
export function visibilityHint(v: string): string {
  return ({
    private: 'Only you can see this project.',
    group:   'Visible to members of the selected group.',
    public:  'Visible to everyone in the workshop.',
  } as Record<string, string>)[v] || '';
}

export function statusLabel(s: string): string {
  return ({ active: 'Active', archived: 'Archived' } as
    Record<string, string>)[s] || s;
}
export function statusPillClass(s: string): string {
  return s === 'active' ? 'good' : 'neutral';
}

/** Offline-first cover art: a theme gradient keyed on the project id, so
 *  cards get stable variety without any external image (the cluster is
 *  offline, so no remote cover images). */
const COVER_GRADIENTS = [
  'linear-gradient(135deg, var(--c-slate) 0%, var(--navy-800) 100%)',
  'linear-gradient(135deg, var(--c-teal) 0%, var(--navy-800) 100%)',
  'linear-gradient(135deg, var(--c-bronze) 0%, var(--navy-800) 100%)',
  'linear-gradient(135deg, var(--navy-500) 0%, var(--navy-800) 100%)',
];
export function projectGradient(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return COVER_GRADIENTS[Math.abs(h) % COVER_GRADIENTS.length];
}

/* client-side formatting from structured gateway values */

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit' });
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return 'n/a';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n; let i = -1;
  do { v /= 1024; i += 1; } while (v >= 1024 && i < units.length - 1);
  return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}
