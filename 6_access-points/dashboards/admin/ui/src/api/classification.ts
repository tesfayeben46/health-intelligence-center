/**
 * Thin fetch wrappers for /gw/classification/*: the column-governance
 * surface (what is masked, what looks sensitive but is not yet classified,
 * steward edits). Same auth pattern as mdm.ts.
 */
import { authFetch } from './useAuth';

const GW = '/gw/classification';

export type MartAttrs = {
  subject: string;
  data_class: string;
  granularity: string;
  default_sensitivity: string;
};

export type ClassifiedColumn = {
  schema_name: string;
  table_name: string;
  column_name: string;
  sensitivity: string;
  mask_class: string;
  data_class: string | null;
  pii: boolean;
  managed_by: string;
  note: string | null;
  updated_at: string | null;
  mart: MartAttrs | null;
};

export type Finding = {
  schema_name: string;
  table_name: string;
  column_name: string;
  reason: string;
};

async function _json<T>(path: string): Promise<T> {
  const r = await authFetch(`${GW}/${path}`);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export function columns(): Promise<{ columns: ClassifiedColumn[]; by_manager: Record<string, number> }> {
  return _json('columns');
}

export function unclassified(): Promise<{ findings: Finding[]; total: number; scanned_columns: number }> {
  return _json('unclassified');
}

export async function classify(body: {
  schema_name: string; table_name: string; column_name: string;
  sensitivity: string; mask_class: string; pii?: boolean; note?: string;
}): Promise<{ ok: boolean }> {
  const r = await authFetch(`${GW}/column`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const b = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${b}`);
  }
  return r.json();
}

export async function remove(schema: string, table: string, column: string): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ schema_name: schema, table_name: table, column_name: column });
  const r = await authFetch(`${GW}/column?${q}`, { method: 'DELETE' });
  if (!r.ok) {
    const b = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${b}`);
  }
  return r.json();
}
