import { WAREHOUSES, REGION_NAMES, PERIODS } from '../data/runtime';
import { COMMODITIES, type Slicer } from '../lib/select';
import { IconRefresh } from '../icons';
import { levelName } from '../../widgets/_shared/reference';

interface Props { slicer: Slicer; setSlice: (p: Partial<Slicer>) => void; reset: () => void }

export default function FilterBar({ slicer, setSlice, reset }: Props) {
  return (
    <div className="pct-filters">
      <label className="pct-field">{levelName('admin1')}
        <select value={slicer.region} onChange={e => setSlice({ region: e.target.value })}>
          <option value="all">All {levelName('admin1', true).toLowerCase()}</option>
          {REGION_NAMES.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
      </label>
      <label className="pct-field">District store
        <select value={slicer.hub} onChange={e => setSlice({ hub: e.target.value })}>
          <option value="all">All district stores</option>
          {WAREHOUSES.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </label>
      <label className="pct-field">Commodity
        <select value={slicer.commodity} onChange={e => setSlice({ commodity: e.target.value })}>
          <option value="all">All commodities</option>
          {COMMODITIES.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
      <label className="pct-field">Planning period
        <select value={slicer.period} onChange={e => setSlice({ period: e.target.value })}>
          {PERIODS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <button className="pct-reset" onClick={reset}><IconRefresh /> Reset filters</button>
    </div>
  );
}
