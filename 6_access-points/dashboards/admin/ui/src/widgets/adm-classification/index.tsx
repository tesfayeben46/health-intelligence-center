/**
 * Column governance: what the platform masks, and what it doesn't yet.
 *
 * Two tabs:
 *   Classified   every protected column, its treatment, and WHO owns the row
 *                (seed = committed cold-boot truth, steward = edited here,
 *                openmetadata = synced from the catalog). Seed rows are shown
 *                read-only: editing them here would be reverted by
 *                the next redeploy, so they are a repo change.
 *   Review       sensitive-looking mart columns with no classification: i.e.
 *                served in the clear right now. Denylist semantics make
 *                omission the only silent leak, so this list is the
 *                counterweight: classify, or dismiss as default/none.
 *
 * A classification takes effect immediately (the gateway's cache is
 * invalidated on write): the next dashboard read is masked by the new policy.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import * as cls from '../../api/classification';
import { useTableKit, TableToolbar, TablePager, SortTh } from '../../kit/tableKit';
import type { AdminWidget } from '../../runtime/types';

const MASK_HINT: Record<string, string> = {
  none: 'visible (classified but unmasked)',
  drop: 'column removed from the response',
  hash: 'irreversible digest, unlinkable across responses',
  partial: 'tail only (•••1234)',
  generalize: 'precision reduced (GPS to region level, dates to month)',
  tokenize: 'stable pseudonym: joins still work',
};

function ownerPill(managedBy: string): string {
  if (managedBy === 'seed') return 'info';
  if (managedBy === 'steward') return 'good';
  return 'watch';
}

function sensPill(s: string): string {
  if (s === 'privileged') return 'alert';
  if (s === 'restricted') return 'watch';
  return 'info';
}

function ClassifiedTab({ tick, onChanged }: { tick: number; onChanged: () => void }) {
  const [rows, setRows] = useState<cls.ClassifiedColumn[] | null>(null);
  const [byManager, setByManager] = useState<Record<string, number>>({});
  const [err, setErr] = useState('');

  const load = useCallback(() => {
    cls.columns()
      .then(r => { setRows(r.columns); setByManager(r.by_manager); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, []);
  useEffect(load, [load, tick]);

  async function drop(c: cls.ClassifiedColumn) {
    try {
      await cls.remove(c.schema_name, c.table_name, c.column_name);
      load(); onChanged();
    } catch (e: any) { setErr(String(e?.message || e)); }
  }

  const kit = useTableKit(rows ?? [], {
    pageSize: 12,
    search: c => `${c.schema_name}.${c.table_name}.${c.column_name} ${c.sensitivity} ${c.mask_class} ${c.managed_by} ${c.note ?? ''}`,
    sortVals: {
      column: c => `${c.schema_name}.${c.table_name}.${c.column_name}`,
      sens: c => c.sensitivity,
      mask: c => c.mask_class,
      owner: c => c.managed_by,
    },
    initialSort: { key: 'column', dir: 'asc' },
  });

  return (
    <>
      <div className="section-hdr">
        <span className="muted text-xs">
          {Object.entries(byManager).map(([k, v]) => `${v} ${k}`).join(' · ') || 'nothing classified'}
        </span>
      </div>
      {err && <p className="muted">Classification unavailable: {err}</p>}
      {rows !== null && rows.length === 0 && !err &&
        <p className="muted">No columns are classified: every mart column is served in the clear.</p>}
      {rows !== null && rows.length > 0 && (
        <>
          <TableToolbar kit={kit} placeholder="Search column, treatment, owner…" unit="columns" />
          <div className="tk-scroll">
            <table className="dash">
              <thead>
                <tr>
                  <SortTh kit={kit} k="column">Column</SortTh>
                  <SortTh kit={kit} k="sens">Sensitivity</SortTh>
                  <SortTh kit={kit} k="mask">Treatment</SortTh>
                  <SortTh kit={kit} k="owner">Owner</SortTh>
                  <th>Note</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {kit.pageRows.map(c => (
                  <tr key={`${c.schema_name}.${c.table_name}.${c.column_name}`}>
                    <td>
                      <div className="v">{c.column_name}</div>
                      <div className="muted text-xs">{c.schema_name}.{c.table_name}
                        {c.mart ? ` · ${c.mart.subject} · ${c.mart.granularity}` : ' · not cataloged'}</div>
                    </td>
                    <td><span className={`pill ${sensPill(c.sensitivity)}`}>{c.sensitivity}</span>
                        {c.pii && <span className="muted text-xs"> PII</span>}</td>
                    <td>
                      <div className="v">{c.mask_class}</div>
                      <div className="muted text-xs">{MASK_HINT[c.mask_class] ?? ''}</div>
                    </td>
                    <td><span className={`pill ${ownerPill(c.managed_by)}`}>{c.managed_by}</span></td>
                    <td className="muted text-xs">{c.note ?? ''}</td>
                    <td>{c.managed_by === 'steward'
                      ? <button className="btn btn-sm btn-ghost" onClick={() => drop(c)}>Unclassify</button>
                      : <span className="muted text-xs">
                          {c.managed_by === 'seed' ? 'repo-managed' : 'catalog-managed'}
                        </span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      )}
    </>
  );
}

function ReviewTab({ onChanged }: { onChanged: () => void }) {
  const [findings, setFindings] = useState<cls.Finding[] | null>(null);
  const [scanned, setScanned] = useState(0);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const [draft, setDraft] = useState<Record<string, { sensitivity: string; mask_class: string }>>({});

  const load = useCallback(() => {
    cls.unclassified()
      .then(r => { setFindings(r.findings); setScanned(r.scanned_columns); setErr(''); })
      .catch(e => setErr(String(e?.message || e)));
  }, []);
  useEffect(load, [load]);

  const keyOf = (f: cls.Finding) => `${f.schema_name}.${f.table_name}.${f.column_name}`;

  async function apply(f: cls.Finding) {
    const k = keyOf(f);
    const d = draft[k] ?? { sensitivity: 'restricted', mask_class: 'generalize' };
    setBusy(k);
    try {
      await cls.classify({
        schema_name: f.schema_name, table_name: f.table_name, column_name: f.column_name,
        sensitivity: d.sensitivity, mask_class: d.mask_class,
        pii: d.sensitivity === 'privileged',
        note: `classified from the review queue (${f.reason})`,
      });
      load(); onChanged();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(''); }
  }

  const kit = useTableKit(findings ?? [], {
    pageSize: 12,
    search: f => `${f.schema_name}.${f.table_name}.${f.column_name} ${f.reason}`,
    sortVals: {
      column: f => `${f.schema_name}.${f.table_name}.${f.column_name}`,
      reason: f => f.reason,
    },
    initialSort: { key: 'column', dir: 'asc' },
  });

  return (
    <>
      <div className="section-hdr">
        <span className="muted text-xs">
          {findings === null ? 'scanning…'
            : `${findings.length} to review · ${scanned} mart columns scanned`}
        </span>
      </div>
      {err && <p className="muted">Scan unavailable: {err}</p>}
      {findings !== null && findings.length === 0 && !err &&
        <p className="muted">Nothing to review: every sensitive-looking mart column is classified.</p>}
      {findings !== null && findings.length > 0 && (
        <>
          <TableToolbar kit={kit} placeholder="Search column, reason…" unit="findings" />
          <div className="tk-scroll">
            <table className="dash">
              <thead>
                <tr>
                  <SortTh kit={kit} k="column">Column</SortTh>
                  <SortTh kit={kit} k="reason">Why flagged</SortTh>
                  <th>Sensitivity</th>
                  <th>Treatment</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {kit.pageRows.map(f => {
                  const k = keyOf(f);
                  const d = draft[k] ?? { sensitivity: 'restricted', mask_class: 'generalize' };
                  const set = (patch: Partial<typeof d>) =>
                    setDraft({ ...draft, [k]: { ...d, ...patch } });
                  return (
                    <tr key={k}>
                      <td>
                        <div className="v">{f.column_name}</div>
                        <div className="muted text-xs">{f.schema_name}.{f.table_name}</div>
                      </td>
                      <td className="text-sm">{f.reason}</td>
                      <td>
                        <select value={d.sensitivity}
                                onChange={e => set({ sensitivity: e.target.value })}>
                          <option value="default">default</option>
                          <option value="restricted">restricted</option>
                          <option value="privileged">privileged</option>
                        </select>
                      </td>
                      <td>
                        <select value={d.mask_class}
                                onChange={e => set({ mask_class: e.target.value })}>
                          {Object.keys(MASK_HINT).map(m => <option key={m} value={m}>{m}</option>)}
                        </select>
                      </td>
                      <td>
                        <button className="btn btn-sm" disabled={busy === k}
                                onClick={() => apply(f)}>
                          {d.sensitivity === 'default' ? 'Dismiss' : 'Classify'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <TablePager kit={kit} />
        </>
      )}
    </>
  );
}

function Classification() {
  const [tab, setTab] = useState<'classified' | 'review'>('classified');
  const [tick, setTick] = useState(0);

  return (
    <div className="card card-elev">
      <div className="section-hdr">
        <h3>Column governance</h3>
        <div>
          <button className={`btn btn-sm ${tab === 'classified' ? '' : 'btn-ghost'}`}
                  onClick={() => setTab('classified')}>Classified</button>{' '}
          <button className={`btn btn-sm ${tab === 'review' ? '' : 'btn-ghost'}`}
                  onClick={() => setTab('review')}>Review queue</button>
        </div>
      </div>
      <p className="muted text-xs">
        A column with no row here is served in the clear. Classifying one takes
        effect on the next read: who sees it unmasked is the role's reveal
        grant, set in the policy, not here.
      </p>
      {tab === 'classified'
        ? <ClassifiedTab tick={tick} onChanged={() => setTick(t => t + 1)} />
        : <ReviewTab onChanged={() => setTick(t => t + 1)} />}
    </div>
  );
}

const widget: AdminWidget = {
  id: 'adm-classification',
  Component: Classification,
};
export default widget;
