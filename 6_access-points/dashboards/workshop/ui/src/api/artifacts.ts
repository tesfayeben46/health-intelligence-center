/**
 * Workshop community-artifacts API client.
 *
 * Backend lives at /api/artifacts in the gateway; workshop's /gw/{path}
 * proxy strips the `api/` prefix, so client paths begin with /gw/artifacts.
 * Source of truth: ops_artifact.artifact (+ artifact_version,
 * artifact_review, artifact_metric): populated by the graduation pathway.
 *
 * Display strings (sizes, counts, dates) are formatted CLIENT-SIDE from
 * the structured numbers the gateway returns: never pre-humanized.
 */
import { authFetch } from './useAuth';

const GW = '/gw/artifacts';

export type ArtifactRow = {
  id: string;
  kind: 'dataset' | 'model' | 'notebook' | string;
  title: string;
  description: string | null;
  tier: 'public' | 'shared' | 'personal' | string;
  license: string;
  citation: string | null;
  tags: string[];
  peer_review_status: 'unreviewed' | 'in_review' | 'approved' | 'rejected' | string;
  workshop_group: string | null;
  // Project scoping (added by 26_artifact_project.sh). Nullable:
  // plugin and unscoped rows carry no project, and models not yet bound to
  // MLflow carry no mlflow_model_name.
  project_id?: string | null;
  mlflow_model_name?: string | null;
  created_at: string;
  updated_at: string;
  owner_username: string | null;
  downloads: number | null;
  views: number | null;
  likes: number | null;
  latest_version: number | null;
  review_count: number;
};

export type ArtifactList = {
  items: ArtifactRow[];
  total: number;
  limit: number;
  offset: number;
};

export type ArtifactVersion = {
  version: number;
  byte_count: number;
  minio_key: string;
  created_at: string;
  sha256: string;
  change_note: string | null;
};

export type ArtifactReview = {
  reviewer: string;                    // kc_sub uuid
  reviewer_username: string | null;
  verdict: 'approved' | 'needs_changes' | 'rejected' | 'pending' | string;
  note: string | null;
  reviewed_at: string;
};

export type ArtifactMetric = {
  downloads: number;
  views: number;
  likes: number;
  last_event_at: string;
};

export type ArtifactDetailData = {
  artifact: ArtifactRow & {
    owner: string;
    // Folded in for kind=model when the MLflow registry knows the title:
    mlflow_name?: string;
    mlflow_stage?: string;
    mlflow_version?: string;
    mlflow_run_id?: string;
  };
  versions: ArtifactVersion[];
  reviews: ArtifactReview[];
  metric: ArtifactMetric | null;
};

export type ListParams = {
  kind?: string;
  tier?: string;
  q?: string;
  owner?: 'me';
  project?: string;                    // project id (uuid): optional scope filter
  sort?: 'recent' | 'name' | 'downloads';
  limit?: number;
  offset?: number;
};

async function _json<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const r = await authFetch(input, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export function listArtifacts(params: ListParams = {}): Promise<ArtifactList> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  }
  const suffix = qs.toString() ? `?${qs}` : '';
  return _json<ArtifactList>(`${GW}${suffix}`);
}

export function getArtifact(id: string): Promise<ArtifactDetailData> {
  return _json<ArtifactDetailData>(`${GW}/${encodeURIComponent(id)}`);
}

/* presentation helpers (real kind/tier vocabulary only) */

export function kindIcon(k: string): string {
  return ({ dataset: 'i-database', model: 'i-cpu', notebook: 'i-file' } as
    Record<string, string>)[k] || 'i-file';
}
export function kindLabel(k: string): string {
  return ({ dataset: 'Dataset', model: 'Model', notebook: 'Notebook' } as
    Record<string, string>)[k] || k;
}
export function tierLabel(t: string): string {
  return ({ public: 'Public', shared: 'Shared', personal: 'Personal' } as
    Record<string, string>)[t] || t;
}
export function reviewStatusLabel(s: string): string {
  return ({ unreviewed: 'unreviewed', in_review: 'in review',
            approved: 'approved', rejected: 'rejected' } as
    Record<string, string>)[s] || s;
}

/** Offline-first cover art: a theme gradient keyed on the artifact kind
 *  (no external images: see the adoption map's offline ruling). */
export function kindGradient(k: string): string {
  return ({
    dataset:  'linear-gradient(135deg, var(--c-slate) 0%, var(--navy-700) 100%)',
    model:    'linear-gradient(135deg, var(--c-teal) 0%, var(--navy-700) 100%)',
    notebook: 'linear-gradient(135deg, var(--c-bronze) 0%, var(--navy-700) 100%)',
  } as Record<string, string>)[k]
    || 'linear-gradient(135deg, var(--navy-500) 0%, var(--navy-700) 100%)';
}

/* client-side formatting from structured gateway numbers */

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return 'n/a';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n; let i = -1;
  do { v /= 1024; i += 1; } while (v >= 1024 && i < units.length - 1);
  return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function fmtCount(n: number | null | undefined): string {
  if (n == null) return 'n/a';
  return n.toLocaleString();
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric' });
}
