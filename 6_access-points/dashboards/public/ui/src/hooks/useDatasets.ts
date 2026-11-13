/**
 * useDatasets: fetch the published Open-Data catalogue from
 * /api/proxy/catalogue (nhdw-static/catalogue.json, materialised from
 * bi_public.* by the publish-datasets job). Falls back to the bundled DATASETS
 * only when the manifest is unavailable. Each entry carries provenance and
 * protection metadata (source system, grain, k>=5, last_refresh) and the
 * direct download URL.
 */
import { useEffect, useState } from 'react';
import { DATASETS, type Dataset } from '../data/datasets';

export type DatasetLive = Dataset & {
  source_system?: string;
  grain?: string;
  k_anonymity?: number;
  last_refresh?: string;
  download_url?: string;
  row_count?: number;
  licence?: string;
};

function fromManifest(d: any): DatasetLive | null {
  if (!d || !d.id || !d.title) return null;
  const bytes = Number(d.size_bytes) || 0;
  const tier = (d.tier === 'bulletin' || d.tier === 'facility-level') ? d.tier : 'aggregate';
  return {
    id: String(d.id),
    title: String(d.title),
    topic: String(d.topic ?? ''),
    tier,
    period: String(d.period ?? 'n/a'),
    formats: Array.isArray(d.formats) ? d.formats.map(String) : ['CSV'],
    sizeMb: Math.max(0.01, Math.round((bytes / 1e6) * 100) / 100),
    updated: String(d.last_refresh ?? '').slice(0, 10),
    downloads: Number(d.row_count) || 0,
    doi: String(d.doi ?? ''),
    desc: String(d.description ?? ''),
    source_system: d.source_system,
    grain: d.grain,
    k_anonymity: d.k_anonymity,
    last_refresh: d.last_refresh,
    download_url: d.download_url,
    row_count: Number(d.row_count) || 0,
    licence: d.licence,
  };
}

export function useDatasets(): DatasetLive[] {
  const [datasets, setDatasets] = useState<DatasetLive[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch('/api/proxy/catalogue')
      .then(r => (r.ok ? r.json() : null))
      .then(body => {
        const arr = Array.isArray(body?.datasets) ? body.datasets : null;
        if (cancelled || !arr) return;
        const mapped = arr.map(fromManifest).filter((d: DatasetLive | null): d is DatasetLive => d !== null);
        if (mapped.length) setDatasets(mapped);
      })
      .catch(() => { /* keep fallback */ });
    return () => { cancelled = true; };
  }, []);
  return datasets ?? (DATASETS as DatasetLive[]);
}
