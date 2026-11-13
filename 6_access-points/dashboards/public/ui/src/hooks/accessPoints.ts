/**
 * Sibling-surface bases: where the public SPA's cross-dashboard links point.
 *
 * The built SPA cannot read env at runtime, so the FastAPI backend resolves the
 * three staff sign-in destinations (Internal BI, Admin, Workshop) and Keycloak
 * from NHDW_BASE_DOMAIN and returns them in the `bases` field of
 * /api/access-points. The result is fetched once and cached module-wide.
 *
 * The fallback is derived from the current origin, never localhost: the bundle
 * is served at public.<base> behind the Ingress, so a sibling host is the same
 * base with the sibling's subdomain (public.127.0.0.1.nip.io becomes
 * internal-bi.127.0.0.1.nip.io). Links stay valid if the backend call fails.
 */

export type SiblingBases = {
  'internal-bi': string;
  admin: string;
  workshop: string;
  keycloak: string;
};

const SUBS: Array<keyof SiblingBases> = ['internal-bi', 'admin', 'workshop', 'keycloak'];

/** Derive sibling bases from the current origin: public.<base> to <sub>.<base>. */
export function fallbackBases(): SiblingBases {
  const { protocol, host } = window.location;   // e.g. public.127.0.0.1.nip.io
  const base = host.replace(/^public\./, '');   // 127.0.0.1.nip.io
  const out = {} as SiblingBases;
  for (const sub of SUBS) out[sub] = `${protocol}//${sub}.${base}`;
  return out;
}

let cached: Promise<SiblingBases> | null = null;

/** Fetch the server-resolved bases once; fall back to same-origin derivation. */
export function loadSiblingBases(): Promise<SiblingBases> {
  if (!cached) {
    cached = fetch('/api/access-points')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(d => {
        const b = d?.bases;
        if (!b || typeof b['internal-bi'] !== 'string') {
          throw new Error('no bases in /api/access-points response');
        }
        return b as SiblingBases;
      })
      .catch(e => {
        console.warn('[access-points] falling back to same-origin bases', e);
        return fallbackBases();
      });
  }
  return cached;
}
