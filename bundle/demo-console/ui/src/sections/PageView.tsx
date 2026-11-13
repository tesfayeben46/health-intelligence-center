import { useMemo, useState } from 'react';
import type { Scenario, DemoPage, Credential } from '../api/demo';
import { triggerScenario } from '../api/demo';
import DemoValue from './DemoValue';

type SortKey = 'value-desc' | 'value-asc' | 'title' | 'category' | 'kind';

const SORT_LABEL: Record<SortKey, string> = {
  'value-desc': 'Demo value · highest first',
  'value-asc':  'Demo value · lowest first',
  'title':      'Title · A -> Z',
  'category':   'Category · A -> Z',
  'kind':       'Direct-control first',
};

export default function PageView({
  page, scenarios, credentials,
}: {
  page: DemoPage | null;
  scenarios: Scenario[];
  credentials: Credential[];
}) {
  const [query, setQuery]     = useState('');
  const [activeCats, setCats] = useState<Set<string>>(new Set());
  const [openId, setOpenId]   = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('value-desc');
  const [onlyDirect, setOnlyDirect] = useState(false);

  const allCats = useMemo(() => {
    const s = new Set<string>();
    for (const sc of scenarios) {
      for (const c of (sc.categories || [sc.category!])) if (c) s.add(c);
    }
    return Array.from(s).sort();
  }, [scenarios]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = scenarios.filter((s) => {
      if (onlyDirect && !s.trigger) return false;
      if (activeCats.size > 0) {
        const cats = new Set(s.categories || [s.category!]);
        let any = false;
        for (const c of activeCats) if (cats.has(c)) { any = true; break; }
        if (!any) return false;
      }
      if (!q) return true;
      const hay = [
        s.id, s.title, s.why_it_matters || '', s.expected_result || '',
        ...(s.steps || []),
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
    return sortScenarios(filtered, sortKey);
  }, [scenarios, query, activeCats, sortKey, onlyDirect]);

  if (!page) {
    return <div className="card"><h2>Page not found</h2></div>;
  }

  function toggleCat(c: string) {
    const next = new Set(activeCats);
    if (next.has(c)) next.delete(c); else next.add(c);
    setCats(next);
  }

  return (
    <>
      <div className="card page-header">
        <div>
          <h2>{page.title}</h2>
          <p className="muted text-sm">{page.description}</p>
        </div>
        <div className="page-header-count">
          <span className="big">{scenarios.length}</span>
          <span className="muted text-xs">scenarios</span>
        </div>
      </div>

      {scenarios.length === 0 && (
        <div className="card">
          <p className="muted">
            No scenarios in this family yet. Add entries with{' '}
            <code>page: {page.id}</code> in{' '}
            <code>bundle/seeds/demo/scenarios.json</code>.
          </p>
        </div>
      )}

      {scenarios.length > 0 && (
        <>
          <div className="card filter-bar">
            <input
              className="search"
              type="search"
              placeholder="Search scenarios…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="sort-select"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              title="Sort scenarios"
            >
              {Object.entries(SORT_LABEL).map(([k, v]) =>
                <option key={k} value={k}>{v}</option>)}
            </select>
            <label className="kind-toggle" title="Show only scenarios with a Trigger button">
              <input type="checkbox"
                checked={onlyDirect}
                onChange={(e) => setOnlyDirect(e.target.checked)} />
              <span>Direct-control only</span>
            </label>
            {allCats.length > 0 && (
              <div className="cat-chips">
                {allCats.map((c) => (
                  <button key={c}
                          className={`chip ${activeCats.has(c) ? 'on' : ''}`}
                          onClick={() => toggleCat(c)}>
                    {c}
                  </button>
                ))}
                {activeCats.size > 0 && (
                  <button className="chip clear" onClick={() => setCats(new Set())}>clear</button>
                )}
              </div>
            )}
            <span className="muted text-sm count-pill">
              {visible.length} of {scenarios.length}
            </span>
          </div>

          <div className="scenario-list">
            {visible.map((s) => (
              <ScenarioRow
                key={s.id}
                scenario={s}
                expanded={openId === s.id}
                onToggle={() => setOpenId(openId === s.id ? null : s.id)}
                credentials={credentials}
              />
            ))}
            {visible.length === 0 && (
              <div className="card empty-state">
                <p className="muted">No scenarios match the current filters.</p>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}

function sortScenarios(arr: Scenario[], key: SortKey): Scenario[] {
  const copy = [...arr];
  switch (key) {
    case 'value-desc':
      return copy.sort((a, b) => (b.demo_value ?? 0) - (a.demo_value ?? 0)
                              || a.title.localeCompare(b.title));
    case 'value-asc':
      return copy.sort((a, b) => (a.demo_value ?? 0) - (b.demo_value ?? 0)
                              || a.title.localeCompare(b.title));
    case 'title':
      return copy.sort((a, b) => a.title.localeCompare(b.title));
    case 'category':
      return copy.sort((a, b) => {
        const ac = (a.categories?.[0] || a.category || '~').toLowerCase();
        const bc = (b.categories?.[0] || b.category || '~').toLowerCase();
        return ac.localeCompare(bc) || a.title.localeCompare(b.title);
      });
    case 'kind':
      return copy.sort((a, b) => (b.trigger ? 1 : 0) - (a.trigger ? 1 : 0)
                              || (b.demo_value ?? 0) - (a.demo_value ?? 0));
  }
}

function ScenarioRow({
  scenario, expanded, onToggle, credentials,
}: {
  scenario: Scenario;
  expanded: boolean;
  onToggle: () => void;
  credentials: Credential[];
}) {
  return (
    <div className={`scenario-card ${expanded ? 'expanded' : ''}`}>
      <div className="row" onClick={onToggle} style={{ cursor: 'pointer' }}>
        <span className="chev">{expanded ? '▾' : '▸'}</span>
        <div className="grow">
          <div className="title">{scenario.title}</div>
          <div className="muted text-sm one-line">
            {scenario.why_it_matters?.split('\n')[0]}
          </div>
        </div>
        <div className="row">
          {scenario.trigger ? (
            <span className="tag good">direct-control</span>
          ) : (
            <span className="tag">instruction</span>
          )}
          <DemoValue value={scenario.demo_value ?? 0} />
        </div>
      </div>

      {expanded && (
        <div className="detail">
          <ScenarioDetail scenario={scenario} credentials={credentials} />
        </div>
      )}
    </div>
  );
}

function ScenarioDetail({ scenario, credentials }: { scenario: Scenario; credentials: Credential[] }) {
  const [triggerResult, setTriggerResult] = useState<{ status: number; body: any } | null>(null);
  const [busy, setBusy] = useState(false);

  const credsNeeded = (scenario.credentials_needed || [])
    .map((u) => credentials.find((c) => c.username === u))
    .filter(Boolean) as Credential[];

  async function onTrigger() {
    setBusy(true);
    try {
      const result = await triggerScenario(scenario.id);
      setTriggerResult(result);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="row" style={{ flexWrap: 'wrap', gap: '0.4em', marginBottom: '0.7em' }}>
        {(scenario.categories || [scenario.category!]).filter(Boolean).map((c) => (
          <span key={c} className="tag accent">{c}</span>
        ))}
        {(scenario.affected_dashboards || []).map((d) => (
          <span key={d} className="tag">{d}</span>
        ))}
      </div>

      {scenario.why_it_matters && (
        <p className="muted text-sm" style={{ whiteSpace: 'pre-wrap', marginBottom: '1em' }}>
          {scenario.why_it_matters}
        </p>
      )}

      {scenario.prerequisites && scenario.prerequisites.length > 0 && (
        <div style={{ marginBottom: '1em' }}>
          <div className="muted text-xs" style={{ marginBottom: '0.3em' }}>Prerequisites</div>
          <ul style={{ margin: 0, paddingLeft: '1.4em' }}>
            {scenario.prerequisites.map((p, i) => <li key={i} className="text-sm">{p}</li>)}
          </ul>
        </div>
      )}

      {credsNeeded.length > 0 && (
        <div style={{ marginBottom: '1em' }}>
          <div className="muted text-xs" style={{ marginBottom: '0.3em' }}>Credentials</div>
          <table style={{ fontSize: '0.88em' }}>
            <tbody>
              {credsNeeded.map((c) => (
                <tr key={c.username}>
                  <td className="mono">{c.username}</td>
                  <td className="mono">{c.password || <span className="muted">-</span>}</td>
                  <td><span className="tag accent">{c.role_label}</span></td>
                  <td className="muted text-sm">{c.realm}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {scenario.steps && (
        <div style={{ marginBottom: '1em' }}>
          <div className="muted text-xs" style={{ marginBottom: '0.3em' }}>Steps</div>
          <ol className="steps">
            {scenario.steps.map((s, i) => <li key={i}>{s}</li>)}
          </ol>
        </div>
      )}

      {scenario.expected_result && (
        <div style={{ marginBottom: '1em' }}>
          <div className="muted text-xs" style={{ marginBottom: '0.3em' }}>Expected result</div>
          <p className="text-sm">{scenario.expected_result}</p>
        </div>
      )}

      {scenario.trigger && (
        <div className="trigger-block">
          <button className="primary" onClick={onTrigger} disabled={busy}>
            {busy ? 'Running…' : 'Trigger'}
          </button>
          <span className="muted text-xs">
            Fires <code>{scenario.trigger.kind}</code>
            {scenario.trigger.args && Object.keys(scenario.trigger.args).length > 0 && (
              <> with <code>{JSON.stringify(scenario.trigger.args)}</code></>
            )}
          </span>
          {triggerResult && (
            <div style={{ marginTop: '0.6em', width: '100%' }}>
              <div className="row" style={{ marginBottom: '0.3em' }}>
                <span className="muted text-sm">HTTP status:</span>
                <span className={triggerResult.status < 300 ? 'tag good'
                  : triggerResult.status < 500 ? 'tag warn' : 'tag bad'}>
                  {triggerResult.status}
                </span>
              </div>
              <div className="trigger-pane">{JSON.stringify(triggerResult.body, null, 2)}</div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
