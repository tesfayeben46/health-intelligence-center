/**
 * One suggestion's source records side by side, field by field, as each
 * source holds them (GET /api/mdm/compare). Shared by Review matches (the
 * steward deciding what to propose) and Approvals (the second person judging
 * the proposal) so both see exactly the same evidence.
 */
import * as mdm from '../../api/mdm';

const RESULT_PILL: Record<mdm.FieldResult, [string, string]> = {
  difference:   ['watch', 'Difference'],
  missing:      ['neutral', 'Missing'],
  not_reported: ['info', 'Not reported'],
  agreement:    ['good', 'Agreement'],
};

export default function Comparison({ cmp }: { cmp: mdm.Comparison }) {
  const systems = [...new Set(cmp.records.map(r => r.source_system))];
  return (
    <>
      <p className="rv-detail-desc">{cmp.records.length} records from {systems.join(', ')}</p>
      <div className="rv-section-h mdm-section">What each source reported</div>
      <div className="tk-scroll">
        <table className="dash mdm-compare">
          <thead>
            <tr>
              <th>Field</th>
              {cmp.records.map(r => (
                <th key={r.source_system + r.record_key} title={r.record_key}>
                  <span className="mdm-src">{r.name ?? 'Record not found'}</span>
                  <span className="mdm-src-sub">
                    {r.source_system} · {r.role === 'suggested' ? 'suggested' : 'in master'}
                  </span>
                </th>
              ))}
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {cmp.fields.map(f => (
              <tr key={f.field}>
                <td>{f.label}</td>
                {f.cells.map((cell, i) => (
                  <td key={i} className={cell.state !== 'value' ? 'mdm-nv'
                    : (f.field === 'coordinates' || f.field === 'code') ? 'mdm-mono' : undefined}>
                    {cell.state === 'value' ? cell.value
                      : cell.state === 'missing' ? 'Missing' : 'Not reported'}
                  </td>
                ))}
                <td><span className={`pill ${RESULT_PILL[f.result][0]}`}>{RESULT_PILL[f.result][1]}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cmp.more_members > 0 &&
        <p className="muted text-sm">{cmp.more_members} more source record{cmp.more_members === 1 ? '' : 's'} in this master.</p>}

      <div className="rv-section-h mdm-section">What agrees and differs</div>
      <div className="mdm-summary">
        {cmp.summary.difference.length === 0 && cmp.summary.missing.length === 0
          ? 'Every field the records share agrees.'
          : <ul>
              {cmp.summary.difference.map(l => <li key={'d' + l}>{l}: different values reported</li>)}
              {cmp.summary.missing.map(l => <li key={'m' + l}>{l}: missing in some records</li>)}
              {cmp.summary.not_reported.map(l => <li key={'n' + l}>{l}: not carried by every source</li>)}
            </ul>}
      </div>
    </>
  );
}
