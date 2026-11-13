/**
 * Widget pub-data-catalogue: the Open Data catalogue (page "data").
 *
 * Stat rail, a control bar (search, tier tabs, topic, sort) and a master-detail
 * view: a paginated dataset list beside a detail panel (key stats,
 * Preview/Schema tabs, Download CSV). Data via useDatasets and
 * /api/proxy/datasets/{id}/preview; falls back to the bundled catalogue when the
 * gateway is unavailable, in which case the preview shows its empty state.
 */
import { Fragment, useEffect, useMemo, useState } from 'react';
import { useDatasets, type DatasetLive } from '../../hooks/useDatasets';
import { useProfile, finestLevel } from '../../hooks/useProfile';
import type { PublicWidget, PublicHost } from '../../runtime/types';

type Tier = 'all' | 'aggregate' | 'facility-level' | 'bulletin';
type Sort = 'updated' | 'downloads' | 'title';
type DetailTab = 'preview' | 'schema';
const PAGE_SIZE = 7;

type Preview = { columns: string[]; rows: string[][]; total_rows: number };

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Human tier label. */
function tierLabel(t: string): string {
  if (t === 'facility-level') return 'Facility';
  if (t === 'bulletin') return 'Bulletin';
  return 'Aggregate';
}

/** Compact "2024 to 2026" range from a verbose period string; falls back to the raw value. */
function compactPeriod(p: string): string {
  if (!p || p === 'n/a') return '';
  const years = p.match(/\d{4}/g);
  if (years && years.length >= 2 && years[0] !== years[years.length - 1]) return `${years[0]} to ${years[years.length - 1]}`;
  if (years && years.length >= 1) return years[0];
  return p;
}

/** Short size label: KB under 1 MB, else MB (one decimal under 10). */
function fmtSize(mb: number): string {
  if (!mb || mb <= 0) return '';
  if (mb < 1) return `${Math.round(mb * 1000)} KB`;
  return `${mb < 10 ? Number(mb.toFixed(1)) : Math.round(mb)} MB`;
}

/** Primary (first) topic. */
function primaryTopic(topic: string): string { return topic.split('·')[0].trim() || ''; }

/** "Jul 2026" from an ISO date; empty string if unparseable. */
function monthYear(iso: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  return m ? `${MONTHS[+m[2] - 1]} ${m[1]}` : '';
}

/** Crude column-type inference from a preview column's sampled values (Schema tab). */
function inferType(col: string, samples: string[]): string {
  const vals = samples.filter(v => v != null && v !== '');
  if (!vals.length) return 'text';
  if (/_id$|^id$/.test(col)) return 'identifier';
  if (/month|date|period|year|_at$/.test(col)) return 'date';
  const allNum = vals.every(v => /^-?[\d,]+(\.\d+)?%?$/.test(String(v).trim()));
  if (allNum) return vals.some(v => String(v).includes('.') || String(v).includes('%')) ? 'decimal' : 'integer';
  return 'text';
}

function DataCatalogue({ host }: { host: PublicHost }) {
  const { openModal } = host;
  const { profile } = useProfile();
  // The aggregation floor named in the licence note: "woreda" in Ethiopia,
  // "district" in Zambia: read from the profile's finest admin level.
  const floorLevel = finestLevel(profile).toLowerCase();
  const [query, setQuery] = useState('');
  const [tier, setTier] = useState<Tier>('all');
  const [topic, setTopic] = useState<string>('all');
  const [sort, setSort] = useState<Sort>('updated');
  const [page, setPage] = useState(0);

  // Master-detail selection + its live preview.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>('preview');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [rowPage, setRowPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(5);

  const DATA = useDatasets();

  const topics = useMemo(
    () => Array.from(new Set(DATA.flatMap(d => d.topic.split('·').map(s => s.trim()).filter(Boolean)))).sort(),
    [DATA]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return DATA
      .filter(d => tier === 'all' || d.tier === tier)
      .filter(d => topic === 'all' || d.topic === topic || d.topic.split('·').map(s => s.trim()).includes(topic))
      .filter(d => !q || d.title.toLowerCase().includes(q) || d.desc.toLowerCase().includes(q) || d.topic.toLowerCase().includes(q))
      .sort((a, b) => {
        if (sort === 'updated')   return (b.updated || '').localeCompare(a.updated || '');
        if (sort === 'downloads') return b.downloads - a.downloads;
        if (sort === 'title')     return a.title.localeCompare(b.title);
        return 0;
      });
  }, [DATA, query, tier, topic, sort]);

  const totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
  const paged = matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Keep a valid selection: default to the first visible dataset, and re-home
  // the selection whenever filters drop the current one from view.
  const selected = matches.find(d => d.id === selectedId) || null;
  useEffect(() => {
    if (!matches.length) { if (selectedId !== null) setSelectedId(null); return; }
    if (!matches.some(d => d.id === selectedId)) setSelectedId(matches[page * PAGE_SIZE]?.id ?? matches[0].id);
  }, [matches, selectedId, page]);

  // Fetch the preview for the selected dataset; without the gateway it stays empty.
  useEffect(() => {
    if (!selected) { setPreview(null); return; }
    let cancelled = false;
    setPreview(null); setPreviewLoading(true); setRowPage(0); setDetailTab('preview');
    (async () => {
      try {
        const r = await fetch(`/api/proxy/datasets/${selected.id}/preview?rows=40`);
        if (r.ok) { const j = await r.json(); if (!cancelled) { setPreview(j); setPreviewLoading(false); } return; }
      } catch { /* no gateway (design harness) */ }
      if (!cancelled) { setPreview(null); setPreviewLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const onAnyFilter = () => setPage(0);
  const clearFilters = () => { setQuery(''); setTier('all'); setTopic('all'); setPage(0); };

  const tiers: { v: Tier; label: string }[] = [
    { v: 'all',            label: 'All' },
    { v: 'aggregate',      label: 'Aggregate' },
    { v: 'bulletin',       label: 'Bulletins' },
    { v: 'facility-level', label: 'Facility' },
  ];

  // Detail preview pagination.
  const rowTotal = preview?.total_rows ?? 0;
  const rowSlice = preview ? preview.rows.slice(rowPage * rowsPerPage, (rowPage + 1) * rowsPerPage) : [];
  const rowFrom = preview && preview.rows.length ? rowPage * rowsPerPage + 1 : 0;
  const rowTo = preview ? Math.min((rowPage + 1) * rowsPerPage, preview.rows.length) : 0;
  const maxRowPage = preview ? Math.max(0, Math.ceil(preview.rows.length / rowsPerPage) - 1) : 0;

  return (
    <section id="downloads" className="od-page">
      <div className="od">
        {/* Page header */}
        <header className="od-head">
          <p className="od-eyebrow">Open Data</p>
          <h1 className="od-title">Public dataset catalog</h1>
          <p className="od-sub">{DATA.length} curated datasets, openly licensed under CC-BY 4.0. Preview in the browser, download as CSV.</p>
        </header>

        {/* Unified control bar */}
        <div className="od-toolbar">
          <span className="od-search">
            <svg className="ico"><use href="#i-search" /></svg>
            <input type="text" placeholder="Search datasets" aria-label="Search datasets"
                   value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
          </span>
          <div className="od-seg" role="group" aria-label="Tier">
            {tiers.map(t => (
              <button key={t.v} className={tier === t.v ? 'is-on' : ''} onClick={() => { setTier(t.v); onAnyFilter(); }}>
                {t.label}
              </button>
            ))}
          </div>
          <div className="od-tools">
            <span className="od-field">
              <svg className="ico"><use href="#i-filter" /></svg>
              <select id="od-topic" aria-label="Topic" value={topic} onChange={(e) => { setTopic(e.target.value); onAnyFilter(); }}>
                <option value="all">All topics</option>
                {topics.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </span>
            <span className="od-field">
              <svg className="ico"><use href="#i-arrow-down" /></svg>
              <select id="od-sort" aria-label="Sort" value={sort} onChange={(e) => { setSort(e.target.value as Sort); onAnyFilter(); }}>
                <option value="updated">Recently updated</option>
                <option value="downloads">Most rows</option>
                <option value="title">Title A to Z</option>
              </select>
            </span>
          </div>
        </div>

        {matches.length === 0 ? (
          <div className="od-empty">
            <svg className="ico" style={{ width: 28, height: 28, color: 'var(--ink-4)' }}><use href="#i-search" /></svg>
            <p>No datasets match the current filters.</p>
            <button className="od-foot-link" onClick={clearFilters}>Clear filters</button>
          </div>
        ) : (
          <div className="od-split">
            {/* MASTER: dataset list */}
            <div className="od-master">
              <ol className="od-list">
                {paged.map((d: DatasetLive) => {
                  const on = d.id === selectedId;
                  const meta = [
                    primaryTopic(d.topic),
                    d.updated ? `Updated ${monthYear(d.updated)}` : '',
                    compactPeriod(d.period),
                    fmtSize(d.sizeMb),
                  ].filter(Boolean);
                  return (
                    <li key={d.id} className={`od-row${on ? ' is-on' : ''}`}>
                      <button className="od-row-btn" onClick={() => setSelectedId(d.id)} aria-pressed={on}>
                        <span className="od-row-ico"><svg className="ico"><use href="#i-database" /></svg></span>
                        <span className="od-row-body">
                          <span className="od-row-line">
                            <span className="od-row-title">{d.title}</span>
                            <span className={`od-tag od-tag--${d.tier}`}>{tierLabel(d.tier)}</span>
                          </span>
                          <span className="od-row-meta">
                            {meta.map((m, i) => (
                              <Fragment key={i}>
                                {i > 0 && <span className="sep">·</span>}
                                <span>{m}</span>
                              </Fragment>
                            ))}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>

              {totalPages > 1 && (
                <div className="od-pager">
                  <button disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹ Prev</button>
                  <span>Page {page + 1} of {totalPages}</span>
                  <button disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next ›</button>
                </div>
              )}
            </div>

            {/* DETAIL: selected dataset */}
            <div className="od-detail">
              {!selected ? (
                <div className="od-detail-empty"><p>Select a dataset to preview it.</p></div>
              ) : (
                <>
                  <div className="od-detail-hd">
                    <div className="od-detail-titles">
                      <h2 className="od-detail-title">{selected.title}</h2>
                      <span className="od-detail-tags">
                        <span className={`od-tag od-tag--${selected.tier}`}>{tierLabel(selected.tier)}</span>
                        {primaryTopic(selected.topic) && <span className="od-tag od-tag--topic">{primaryTopic(selected.topic)}</span>}
                      </span>
                    </div>
                    {selected.download_url ? (
                      <a href={selected.download_url} className="od-dl-btn" download>
                        <svg className="ico"><use href="#i-download" /></svg> Download CSV
                      </a>
                    ) : (
                      <a href="#" className="od-dl-btn" onClick={(e) => e.preventDefault()}>
                        <svg className="ico"><use href="#i-download" /></svg> Download CSV
                      </a>
                    )}
                  </div>

                  {selected.desc && <p className="od-detail-desc">{selected.desc}</p>}

                  {/* Key stats: one quiet row */}
                  <div className="od-detail-stats">
                    <div className="od-dstat"><em>Rows</em><b>{(selected.row_count || rowTotal) ? (selected.row_count || rowTotal).toLocaleString() : 'n/a'}</b></div>
                    <div className="od-dstat"><em>Columns</em><b>{preview ? preview.columns.length : 'n/a'}</b></div>
                    <div className="od-dstat"><em>Updated</em><b>{selected.updated ? monthYear(selected.updated) : 'n/a'}</b></div>
                    <div className="od-dstat"><em>License</em><b>{selected.licence || 'CC-BY 4.0'}</b></div>
                  </div>

                  {/* Tabs */}
                  <div className="od-detail-tabs" role="tablist">
                    <button role="tab" aria-selected={detailTab === 'preview'} className={detailTab === 'preview' ? 'is-on' : ''} onClick={() => setDetailTab('preview')}>Preview</button>
                    <button role="tab" aria-selected={detailTab === 'schema'} className={detailTab === 'schema' ? 'is-on' : ''} onClick={() => setDetailTab('schema')}>Schema</button>
                  </div>

                  {previewLoading ? (
                    <p className="od-preview-note">Loading preview…</p>
                  ) : !preview ? (
                    <p className="od-preview-note">No preview available for this dataset.</p>
                  ) : detailTab === 'preview' ? (
                    <>
                      <div className="od-preview-scroll">
                        <table className="od-preview-table">
                          <thead><tr>{preview.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
                          <tbody>
                            {rowSlice.map((row, ri) => (
                              <tr key={ri}>{row.map((v, ci) => <td key={ci}>{v}</td>)}</tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="od-preview-foot">
                        <span className="od-rowsize">
                          <select aria-label="Rows per page" value={rowsPerPage}
                                  onChange={(e) => { setRowsPerPage(Number(e.target.value)); setRowPage(0); }}>
                            <option value={5}>Show 5 rows</option>
                            <option value={10}>Show 10 rows</option>
                            <option value={20}>Show 20 rows</option>
                          </select>
                          <svg className="ico"><use href="#i-chevron-down" /></svg>
                        </span>
                        <span className="od-preview-foot-r">
                          <span className="od-preview-note">{rowFrom} to {rowTo} of {rowTotal.toLocaleString()}</span>
                          <span className="od-rowpager">
                            <button disabled={rowPage === 0} onClick={() => setRowPage(p => p - 1)} aria-label="Previous rows">
                              <svg className="ico"><use href="#i-chevron-right" /></svg></button>
                            <button disabled={rowPage >= maxRowPage} onClick={() => setRowPage(p => p + 1)} aria-label="Next rows">
                              <svg className="ico"><use href="#i-chevron-right" /></svg></button>
                          </span>
                        </span>
                      </div>
                    </>
                  ) : (
                    <ul className="od-schema">
                      {preview.columns.map((c, ci) => (
                        <li key={c} className="od-schema-row">
                          <span className="od-schema-name">{c}</span>
                          <span className="od-schema-type">{inferType(c, preview.rows.map(r => r[ci]))}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        <div className="od-foot">
          <span className="od-foot-note">
            <svg className="ico"><use href="#i-shield" /></svg>
            <span className="od-foot-note-txt">
              <strong>CC-BY 4.0 · k-anonymity of at least 5 · aggregated to {floorLevel} or coarser</strong>
              <span>Data are freely reusable with attribution. Facility-level data require additional approval.</span>
            </span>
          </span>
          <button className="od-foot-link" onClick={() => openModal('access')}>
            Request facility-level access <svg className="ico"><use href="#i-arrow-right" /></svg>
          </button>
        </div>
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-data-catalogue', Component: DataCatalogue };
export default widget;
