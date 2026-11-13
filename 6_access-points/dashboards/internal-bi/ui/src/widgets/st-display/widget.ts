/** st-display: per-user display preferences.
 *
 * These are client-only presentation prefs (no gateway endpoint):
 * default period, numeric format, auto-refresh, compact density,
 * remember-filters. Persisted to localStorage under `nhdw.bi.display.*` so
 * they survive reload. Compact
 * density has a live side-effect on <body> (density-compact class), kept in
 * sync with biApp.compactDensity for any code that reads it. */
import type { WidgetDef } from '../../runtime/types';
import { PERIOD_OPTS, getBiApp } from '../../biApp';

const K = {
  period: 'nhdw.bi.display.period',
  numfmt: 'nhdw.bi.display.numfmt',
  autoRefresh: 'nhdw.bi.display.autoRefresh',
  density: 'nhdw.bi.display.density',
  rememberFilters: 'nhdw.bi.display.rememberFilters',
};
const getBool = (k: string, dflt: boolean) => { const v = localStorage.getItem(k); return v == null ? dflt : v === '1'; };
const getStr = (k: string, dflt: string) => localStorage.getItem(k) ?? dflt;

const def: WidgetDef = {
  id: 'st-display',
  reactsTo: [],
  render(ctx) {
    const { theme } = ctx;
    const period = getStr(K.period, 'last_12mo');
    const numfmt = getStr(K.numfmt, '1,234.56');
    const autoRefresh = getBool(K.autoRefresh, true);
    const density = getBool(K.density, false);
    const remember = getBool(K.rememberFilters, true);

    const periodOpts = Object.entries(PERIOD_OPTS)
      .map(([k, lbl]) => `<option value="${k}"${k === period ? ' selected' : ''}>${lbl}</option>`).join('');

    const check = (id: string, label: string, on: boolean, extra = '') => `
      <label style="display:flex;align-items:center;gap:.5rem;cursor:pointer;margin-top:.4rem">
        <input type="checkbox" data-pref="${id}"${on ? ' checked' : ''}/> ${label}${extra}
      </label>`;

    ctx.el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.0rem 1.2rem;max-width:760px">
        <div class="field-group" style="margin:0">
          <label>Default period</label>
          <select data-pref="period">${periodOpts}</select>
        </div>
        <div class="field-group" style="margin:0">
          <label>Numeric format</label>
          <select data-pref="numfmt">
            <option${numfmt === '1,234.56' ? ' selected' : ''}>1,234.56</option>
            <option${numfmt === '1 234,56' ? ' selected' : ''}>1 234,56</option>
          </select>
        </div>
        <div class="field-group" style="margin:0;grid-column:span 2">
          ${check('autoRefresh', 'Auto-refresh data every 5 minutes', autoRefresh)}
          ${check('density', 'Compact density (more content per screen)', density,
            `<span data-density-note style="font-size:.72rem;color:${theme.ink[2]}${density ? '' : ';display:none'}"> (active, click to revert)</span>`)}
          ${check('rememberFilters', 'Remember last filter selections across sessions', remember)}
        </div>
        <p style="grid-column:span 2;margin:.1rem 0 0;font-size:.78rem;color:${theme.ink[2]}">
          Saved on this device; these preferences don't leave your browser.
        </p>
      </div>`;

    const applyDensity = (on: boolean) => {
      document.body.classList.toggle('density-compact', on);
      const app: any = getBiApp();
      if (app) app.compactDensity = on;
      const note = ctx.el.querySelector<HTMLElement>('[data-density-note]');
      if (note) note.style.display = on ? '' : 'none';
    };
    // Apply persisted density immediately so a returning user sees it.
    applyDensity(density);

    ctx.el.querySelectorAll<HTMLSelectElement>('select[data-pref]').forEach(sel => {
      sel.addEventListener('change', () => {
        localStorage.setItem(sel.dataset.pref === 'period' ? K.period : K.numfmt, sel.value);
      });
    });
    ctx.el.querySelectorAll<HTMLInputElement>('input[data-pref]').forEach(inp => {
      inp.addEventListener('change', () => {
        const key = ({ autoRefresh: K.autoRefresh, density: K.density, rememberFilters: K.rememberFilters } as any)[inp.dataset.pref!];
        localStorage.setItem(key, inp.checked ? '1' : '0');
        if (inp.dataset.pref === 'density') applyDensity(inp.checked);
      });
    });
  },
};
export default def;
