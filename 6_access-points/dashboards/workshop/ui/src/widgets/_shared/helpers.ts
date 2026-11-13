/**
 * Shared helpers for workshop widgets: escaping, client-side formatting of
 * STRUCTURED gateway values (never pre-humanized server strings), response
 * unwrapping, and the toast. Widgets in this dashboard reuse these; other
 * dashboards copy, never import (repetition over shared coupling).
 */

/** Escape every dynamic string before it lands in innerHTML. */
export const esc = (s: unknown): string =>
  String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c] as string));

/* formatting (structured values to display strings, client-side) */

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

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return 'n/a';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined,
    { year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit' });
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return esc(iso);
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/* response plumbing */

/** Unwrap the assorted list envelopes ops routes return: a bare array,
 *  `{ items }`, `{ rows }`, or a named key (`{ groups }`, `{ projects }`,
 *  `{ models }`, …). Returns [] when there is no list. */
export function unwrapList<T = any>(j: unknown, ...keys: string[]): T[] {
  if (Array.isArray(j)) return j as T[];
  if (j && typeof j === 'object') {
    const o = j as Record<string, unknown>;
    for (const k of [...keys, 'items', 'rows', 'results']) {
      if (Array.isArray(o[k])) return o[k] as T[];
    }
  }
  return [];
}

/** Read the real error text out of a non-ok Response (FastAPI's {detail},
 *  a JSON error field, or the raw body): surfaced verbatim. */
export async function errText(r: Response): Promise<string> {
  let body = '';
  try { body = await r.text(); } catch { /* keep '' */ }
  try {
    const j = JSON.parse(body);
    const d = j?.detail ?? j?.error ?? j?.message;
    if (typeof d === 'string' && d) return d;
    if (d != null) return JSON.stringify(d);
  } catch { /* not JSON */ }
  return body || `${r.status} ${r.statusText}`;
}

/* toast (uses the existing .wk-toast styling) */

let toastEl: HTMLElement | null = null;
let toastTimer: number | undefined;

/** Show a transient confirmation toast (bottom of the viewport). */
export function toast(text: string): void {
  if (toastEl) { toastEl.remove(); toastEl = null; }
  if (toastTimer) window.clearTimeout(toastTimer);
  const el = document.createElement('div');
  el.className = 'wk-toast';
  el.innerHTML = `<svg class="ico"><use href="#i-check"/></svg><span>${esc(text)}</span>`;
  el.addEventListener('click', () => { el.remove(); if (toastEl === el) toastEl = null; });
  document.body.appendChild(el);
  toastEl = el;
  toastTimer = window.setTimeout(() => {
    el.remove();
    if (toastEl === el) toastEl = null;
  }, 6000);
}

/* artifact vocabulary (real kinds/tiers only) */

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
