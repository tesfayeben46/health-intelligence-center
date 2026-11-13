/**
 * Thin fetch wrapper for /gw/docs, the admin Documentation page's data source.
 * The gateway serves the curated operator docs from the private object-store
 * bucket nhdw-docs (SeaweedFS, S3 API; seeded at boot from docs/*.md).
 * Role-gated to R-OPS-* by the gateway.
 *
 * The admin /gw/{path} proxy prepends /api/, so /gw/docs to GET /api/docs.
 */
import { authFetch } from './useAuth';

const GW = '/gw/docs';

async function _json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(`${GW}${path}`, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type DocMeta = {
  name: string;
  title: string;
  section: string;
  description: string;
  size: number;
};
export type DocsResp = { docs: DocMeta[]; bucket: string };
export type DocContent = { name: string; markdown: string };

export const fetchDocs = () => _json<DocsResp>('');
export const fetchDoc = (name: string) =>
  _json<DocContent>(`/${encodeURIComponent(name)}`);
export const fetchDocDownloadUrl = (name: string) =>
  _json<{ name: string; url: string }>(`/${encodeURIComponent(name)}?download=1`);
