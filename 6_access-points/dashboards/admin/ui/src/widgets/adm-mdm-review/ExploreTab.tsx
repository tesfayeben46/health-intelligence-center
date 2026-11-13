/**
 * Explore: the governed master-data lists, one list, one record.
 *
 *   lists    the lists that have a stewardship lane (GET /api/mdm/overview)
 *   list     Facilities: one row per master, searched, filtered and paged in
 *            the warehouse (GET /api/mdm/facilities)
 *   record   one master: its history across five lanes beside the golden
 *            record, source members and identifiers (GET /api/mdm/master/…
 *            and …/history)
 *
 * Moving along the timeline changes only what is highlighted, never the record.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as mdm from '../../api/mdm';
import { LoadError, fmtNum, fmtWhen } from './parts';

type View = { name: 'lists' } | { name: 'list' } | { name: 'record'; bk: string };

const PAGE = 25;

const REGISTRY: Record<Exclude<mdm.Registry, 'all'>, [string, string]> = {
  linked:         ['good', 'Linked'],
  possible_match: ['watch', 'Possible match'],
  single_source:  ['neutral', 'Single source'],
};

const MATCHED_BY: Record<string, string> = {
  facility_id: 'ID match',
  norm_name: 'Name match',
  steward_decision: 'Steward decision',
};

const LANES: Array<[mdm.HistoryEvent['lane'], string]> = [
  ['sources', 'Sources'],
  ['matching', 'Matching'],
  ['stewardship', 'Stewardship'],
  ['master', 'Master record'],
  ['releases', 'Releases'],
];

/* lists */
function Lists({ open }: { open: () => void }) {
  const [d, setD] = useState<mdm.ListOverview[] | null>(null);
  const [err, setErr] = useState('');
  const load = useCallback(() => {
    mdm.overview().then(r => { setD(r.lists); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, []);
  useEffect(load, [load]);

  if (err) return <LoadError what="master data lists" err={err} retry={load} />;
  if (!d) return <p className="muted">Loading master data lists…</p>;
  if (d.length === 0) return <p className="muted">No master data lists are available.</p>;
  return (
    <div className="mdm-lists">
      {d.map(l => {
        const published = l.last_release?.masters ?? 0;
        const body = (
          <>
            <div className="card-hdr"><h4>{l.title}</h4></div>
            <span className="mdm-count">
              {fmtNum(l.masters)} masters · {fmtNum(published)} published · {fmtNum(l.waiting)} to review
            </span>
            {l.sources.length > 0 &&
              <span className="mdm-count muted">
                {l.sources.map(s => `${s.source_system} ${fmtNum(s.n)}`).join(' · ')}
              </span>}
            <span className="mdm-count muted">
              {l.last_release ? `Last release ${l.last_release.release_id}` : 'No release yet'}
            </span>
          </>
        );
        return l.entity_class === 'facility'
          ? <button key={l.entity_class} className="card mdm-list-card mdm-list-open" onClick={open}
                    aria-label={`Open ${l.title}`}>{body}</button>
          : <div key={l.entity_class} className="card mdm-list-card">{body}</div>;
      })}
    </div>
  );
}

/* list */
function FacilityList({ back, openRecord }: { back: () => void; openRecord: (bk: string) => void }) {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [registry, setRegistry] = useState<mdm.Registry>('all');
  const [offset, setOffset] = useState(0);
  const [d, setD] = useState<mdm.FacilityPage | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {                          // search after typing stops
    const t = window.setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(t);
  }, [q]);

  const load = useCallback(() => {
    let live = true;
    setErr('');
    mdm.facilities(query, registry, PAGE, offset)
      .then(r => { if (live) setD(r); })
      .catch(e => { if (live) setErr(String(e?.message || e)); });
    return () => { live = false; };
  }, [query, registry, offset]);
  useEffect(load, [load]);

  const s = d?.summary;
  const pages = d ? Math.max(1, Math.ceil(d.total / PAGE)) : 1;
  const page = Math.floor(offset / PAGE) + 1;
  return (
    <>
      <button className="mdm-link mdm-back" onClick={back}>← Explore</button>
      <div className="section-hdr mdm-hdr">
        <h3>Facilities</h3>
        {s && <span className="meta">
          {fmtNum(s.linked)} linked · {fmtNum(s.possible_match)} with a possible match · {fmtNum(s.single_source)} single source
        </span>}
      </div>
      <div className="card card-elev">
        <div className="tk-toolbar">
          <span className="search-row tk-search">
            <span className="search-ico"><svg className="ico ico-sm"><use href="#i-search" /></svg></span>
            <input className="search-input" aria-label="Search facilities by name"
                   placeholder="Search facilities by name" value={q} onChange={e => setQ(e.target.value)} />
          </span>
          <select value={registry} aria-label="In registry"
                  onChange={e => { setRegistry(e.target.value as mdm.Registry); setOffset(0); }}>
            <option value="all">Any status</option>
            <option value="linked">Linked</option>
            <option value="possible_match">Possible match</option>
            <option value="single_source">Single source</option>
          </select>
        </div>
        {err && <LoadError what="Facilities" err={err} retry={load} />}
        {!d && !err && <p className="muted">Loading Facilities…</p>}
        {d && d.facilities.length === 0 && !err &&
          <p className="muted">{query || registry !== 'all' ? 'No records match these filters.' : 'No records in this list yet.'}</p>}
        {d && d.facilities.length > 0 && (
          <>
            <div className="tk-scroll tk-scroll-tall">
              <table className="dash">
                <thead>
                  <tr><th>Name</th><th>Status</th><th>Woreda</th><th>Zone</th><th>Sources</th><th>In registry</th><th><span className="sr-only">Open</span></th></tr>
                </thead>
                <tbody>
                  {d.facilities.map(f => (
                    <tr key={f.master_bk}>
                      <td><div className="v">{f.name ?? f.master_bk}</div>
                          {f.master_bk.startsWith('FAC-') &&
                            <div className="muted text-xs mdm-mono">{f.master_bk}</div>}</td>
                      <td>{f.operational_status ?? <span className="muted">n/a</span>}</td>
                      <td>{f.woreda ?? <span className="muted">n/a</span>}</td>
                      <td>{f.zone ?? <span className="muted">n/a</span>}</td>
                      <td className="text-sm">{f.sources ?? 'n/a'}</td>
                      <td><span className={`pill ${REGISTRY[f.registry][0]}`}>{REGISTRY[f.registry][1]}</span></td>
                      <td><button className="btn btn-sm btn-ghost" onClick={() => openRecord(f.master_bk)}>Open</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="tk-pager">
              <span className="muted">{fmtNum(offset + 1)} to {fmtNum(Math.min(d.total, offset + PAGE))} of {fmtNum(d.total)}</span>
              <span className="tk-spacer" />
              <button className="btn btn-sm btn-ghost" disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}>‹ Prev</button>
              <span className="tk-pageno muted">page {page} / {pages}</span>
              <button className="btn btn-sm btn-ghost" disabled={offset + PAGE >= d.total}
                      onClick={() => setOffset(offset + PAGE)}>Next ›</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

/* record */
function Timeline({ h }: { h: mdm.MasterHistory }) {
  const ev = h.events;
  const [at, setAt] = useState(ev.length - 1);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);
  const stop = () => {
    if (timer.current) { window.clearInterval(timer.current); timer.current = null; }
    setPlaying(false);
  };
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);
  useEffect(() => { setAt(ev.length - 1); }, [ev.length]);

  function play() {
    stop();
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { setAt(ev.length - 1); return; }
    setAt(0);
    setPlaying(true);
    let i = 0;
    timer.current = window.setInterval(() => {
      i += 1;
      if (i >= ev.length) { stop(); return; }
      setAt(i);
    }, 800);
  }

  if (ev.length === 0) return <p className="muted">No history events are available.</p>;
  const cur = ev[Math.max(0, at)];
  // a source record and a golden revision stay in force once they appear
  const keeps = (lane: string) => lane === 'sources' || lane === 'master';

  return (
    <>
      <div className="mdm-tl-bar">
        <span className="text-sm">As of <strong>{fmtWhen(cur.at)}</strong></span>
        <span className="rv-actions">
          <button className="btn btn-sm btn-ghost" disabled={at <= 0} onClick={() => { stop(); setAt(at - 1); }}>Earlier</button>
          <button className="btn btn-sm btn-ghost" disabled={at >= ev.length - 1} onClick={() => { stop(); setAt(at + 1); }}>Later</button>
          <button className="btn btn-sm btn-ghost" onClick={playing ? stop : play}>{playing ? 'Pause' : 'Play'}</button>
          <button className="btn btn-sm" onClick={() => { stop(); setAt(ev.length - 1); }}>Now</button>
        </span>
      </div>
      <div className="tk-scroll mdm-tl-box">
        <div className="mdm-tl" style={{ gridTemplateColumns: `150px repeat(${ev.length}, minmax(44px, 1fr))` }}
             role="group" aria-label="History by lane">
          {LANES.map(([lane, label]) => {
            const firstIdx = ev.findIndex(e => e.lane === lane);
            return [
              <div key={lane} className="mdm-tl-name">{label}</div>,
              ...ev.map((e, i) => {
                const on = keeps(lane) && firstIdx >= 0 && i >= firstIdx;
                const cls = ['mdm-tl-cell', on ? 'on' : '', on && i === firstIdx ? 'start' : '',
                             i === at ? 'asof' : '', i > at ? 'later' : ''].filter(Boolean).join(' ');
                return (
                  <div key={lane + i} className={cls}>
                    {e.lane === lane &&
                      <button className={`mdm-tl-dot${i === at ? ' sel' : ''}`}
                              aria-label={`Event ${i + 1}: ${e.title}`}
                              onClick={() => { stop(); setAt(i); }}>{i + 1}</button>}
                  </div>
                );
              }),
            ];
          })}
        </div>
      </div>
      <div className="mdm-events">
        {ev.map((e, i) => (
          <button key={i} className={`mdm-event${i === at ? ' sel' : ''}`} onClick={() => { stop(); setAt(i); }}>
            <span className="mdm-event-n">{i + 1}</span>
            <span className="muted text-sm">{fmtWhen(e.at)}</span>
            <span><span className="v">{e.title}</span>
                  {e.detail && <span className="muted text-xs mdm-block">{e.detail}</span>}</span>
          </button>
        ))}
      </div>
    </>
  );
}

function Record({ bk, back }: { bk: string; back: () => void }) {
  const [m, setM] = useState<mdm.MasterDetail | null>(null);
  const [h, setH] = useState<mdm.MasterHistory | null>(null);
  const [err, setErr] = useState('');

  const load = useCallback(() => {
    let live = true;
    setErr(''); setM(null); setH(null);
    Promise.all([mdm.master('facility', bk), mdm.history('facility', bk)])
      .then(([a, b]) => { if (live) { setM(a); setH(b); } })
      .catch(e => { if (live) setErr(String(e?.message || e)); });
    return () => { live = false; };
  }, [bk]);
  useEffect(load, [load]);

  const g = (m?.golden ?? null) as Record<string, any> | null;
  const name = g?.canonical_name ?? m?.members[0]?.source_name ?? bk;
  const golden: Array<[string, any, string | undefined]> = g ? [
    ['Name', g.canonical_name, g.name_source],
    ['Operational status', g.operational_status, g.status_source],
    ['Phone', g.telecom, g.telecom_source],
    ['Coordinates', g.latitude != null && g.longitude != null ? `${g.latitude}, ${g.longitude}` : null, g.coord_source],
    ['Woreda', g.woreda_name, g.admin_source],
    ['Zone', g.zone_name, g.admin_source],
  ] : [];

  return (
    <>
      <button className="mdm-link mdm-back" onClick={back}>← Facilities</button>
      {err && !err.startsWith('404') && <LoadError what="this record" err={err} retry={load} />}
      {err.startsWith('404') && <p className="muted">Record not found.</p>}
      {!err && (!m || !h) && <p className="muted">Loading record history…</p>}
      {m && h && (
        <>
          <div className="mdm-q mdm-record-hdr">
            <h3 className="rv-detail-name mdm-name">{name}</h3>
            {h.published_state === 'published'
              ? <span className="pill good">In release {h.latest_release}</span>
              : <span className="pill neutral">Not yet published</span>}
            <span className="muted text-sm mdm-mono">{bk}</span>
          </div>
          <div className="mdm-record">
            <div className="card card-elev">
              <div className="card-hdr"><h4>Record history</h4></div>
              <Timeline h={h} />
            </div>
            <div className="card">
              <div className="card-hdr"><h4>Golden record</h4></div>
              {golden.length === 0
                ? <p className="muted">No golden record yet.</p>
                : <dl className="mdm-kv">
                    {golden.map(([label, value, src]) => [
                      <dt key={label + 'k'}>{label}</dt>,
                      <dd key={label + 'v'}>{value ?? <span className="muted">Not recorded</span>}
                        {value != null && src && <span className="muted text-xs mdm-block">from {src}</span>}</dd>,
                    ])}
                  </dl>}
              <div className="rv-section-h mdm-section">Sources</div>
              {m.members.map(s => (
                <div key={s.source_system + s.source_record_key} className="mdm-member">
                  <span><span className="v">{s.source_name || 'n/a'}</span>
                        <span className="muted text-xs mdm-block">{s.source_system}</span></span>
                  <span className="muted text-xs">{MATCHED_BY[s.match_strategy] ?? s.match_strategy}</span>
                </div>
              ))}
              <div className="rv-section-h mdm-section">Known as</div>
              {m.identifiers.length === 0
                ? <p className="muted text-sm mdm-flush">No identifiers recorded.</p>
                : <dl className="mdm-kv">
                    {m.identifiers.map(i => [
                      <dt key={i.identifier_namespace + i.identifier_value + 'k'}>{i.identifier_namespace}</dt>,
                      <dd key={i.identifier_namespace + i.identifier_value + 'v'} className="mdm-mono">{i.identifier_value}</dd>,
                    ])}
                  </dl>}
              {m.decisions.length > 0 && (
                <>
                  <div className="rv-section-h mdm-section">Standing decisions</div>
                  {m.decisions.map((d, i) => (
                    <p key={i} className="text-sm mdm-flush">
                      {d.decision === 'accept' ? 'Same facility' : d.decision === 'reject' ? 'Different facilities' : d.decision}
                      {' · '}{d.source_system_code} · {d.decided_by}
                      <span className="muted"> · {fmtWhen(d.recorded_from)}</span>
                    </p>
                  ))}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </>
  );
}

export default function ExploreTab({ openBk, onOpened }: { openBk: string; onOpened: () => void }) {
  const [view, setView] = useState<View>(openBk ? { name: 'record', bk: openBk } : { name: 'lists' });
  useEffect(() => {
    if (openBk) { setView({ name: 'record', bk: openBk }); onOpened(); }
  }, [openBk]);

  if (view.name === 'lists') return <Lists open={() => setView({ name: 'list' })} />;
  if (view.name === 'list')
    return <FacilityList back={() => setView({ name: 'lists' })}
                         openRecord={bk => setView({ name: 'record', bk })} />;
  return <Record bk={view.bk} back={() => setView({ name: 'list' })} />;
}
