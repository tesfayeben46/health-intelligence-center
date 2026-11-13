/**
 * Widget-data seam: data_url-first fetch of gateway-resolved widget data.
 *
 * Copied per SPA (dashboard rule: repetition over shared
 * coupling: do not extract into a shared module). The public SPA is
 * anonymous: it calls its own FastAPI backend's
 * /api/proxy/widget-data/{id}, which forwards server-side to the
 * gateway's /api/permissions/widget/{id}/data with no auth header: the
 * gateway maps that to R-PUB-ANON and enforces the widget grant + OPA
 * mart gate before touching any mart.
 *
 * Returns null on any failure (network, non-200, permitted-stub, empty
 * rows) so callers keep rendering their bundled fallback data.
 */
import { useEffect, useState } from 'react';

export type WidgetRow = Record<string, unknown>;
export type WidgetData = { columns: string[]; rows: WidgetRow[] };

export async function fetchWidgetData(widgetId: string): Promise<WidgetData | null> {
  try {
    const r = await fetch(`/api/proxy/widget-data/${widgetId}`);
    if (!r.ok) {
      console.warn(`[widget-data] ${widgetId}: HTTP ${r.status}`);
      return null;
    }
    const body = await r.json();
    const data = body?.data;
    if (!data || !Array.isArray(data.columns) || !Array.isArray(data.rows)
        || data.rows.length === 0) {
      return null;                 // permitted-stub or empty mart: use static
    }
    return { columns: data.columns, rows: data.rows };
  } catch (e) {
    console.warn(`[widget-data] ${widgetId} fetch error`, e);
    return null;
  }
}

/** React hook: fetches once per widget id; null until (and unless) real
 *  rows arrive, so components fall back to their static data. */
export function useWidgetData(widgetId: string): WidgetData | null {
  const [data, setData] = useState<WidgetData | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchWidgetData(widgetId).then(d => {
      if (!cancelled && d) setData(d);
    });
    return () => { cancelled = true; };
  }, [widgetId]);
  return data;
}
