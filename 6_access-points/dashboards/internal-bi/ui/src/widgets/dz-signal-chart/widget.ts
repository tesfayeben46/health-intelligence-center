/** EWARS alarm signal from bi_idsr.mart_ml_disease_input joined with model output.
 *  Observed vs model mean, 95% band, alarm threshold and flags, per week for the
 *  focal disease. The fetcher returns all diseases by weeks for the geo scope; the
 *  widget picks the focal disease (the disease selected in the programme filter,
 *  else the one with the highest latest observed count). */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import { timeSeriesBand, type SignalSeries } from '../_shared/charts';
import { DISEASE_NAME, PROGRAMMES, periodWeeks, signalModelId, isoWeekOrd } from '../_shared/reference';

const MODEL_OPTS = [
  { id: 'ears_c1', label: 'EARS C1 (endemic channel)' },
  { id: 'farrington', label: 'Farrington flexible' },
  { id: 'seasonal_naive', label: 'Seasonal-naive' },
  { id: 'ewars_csd', label: 'EWARS-CSD (climate-driven)' },
];

/** Pick the focal disease's series from the flat rows. */
function pickSeries(rows: Record<string, any>[], programme: string): { label: string; series: SignalSeries } | null {
  const weekOf = (r: any) => r.iso_week ?? (r.year != null && r.week != null ? `${r.year}W${r.week}` : '');
  const byLabel: Record<string, any[]> = {};
  rows.forEach(r => { const l = String(r.disease_label ?? DISEASE_NAME[r.disease_code] ?? r.disease_code ?? '-'); (byLabel[l] ||= []).push(r); });
  const locked = programme !== 'all' && !PROGRAMMES.some((p: { code: string }) => p.code === programme);
  const stem = (locked ? (DISEASE_NAME[programme] || programme) : '').toLowerCase().split(' ')[0];
  let label = stem ? Object.keys(byLabel).find(l => l.toLowerCase().includes(stem)) : undefined;
  if (!label) {
    if (locked) return null;   // a selected disease with no matching series renders empty
    let best = -1;
    for (const l of Object.keys(byLabel)) { const rs = byLabel[l]; const v = +(rs[rs.length - 1]?.observed ?? 0); if (v > best) { best = v; label = l; } }
  }
  if (!label) return null;
  const rs = byLabel[label].slice().sort((a, b) => isoWeekOrd(weekOf(a)) - isoWeekOrd(weekOf(b)));
  const num = (v: any) => (v == null ? null : +v);
  const series: SignalSeries = {
    weeks: rs.map(weekOf),
    observed: rs.map(r => +(r.observed ?? 0) || 0),
    predicted: rs.map(r => num(r.pred_cases_mean)),
    lwr: rs.map(r => { const v = num(r.pred_cases_lwr95); return v == null ? null : Math.max(0, v); }),
    upr: rs.map(r => num(r.pred_cases_upr95)),
    threshold: rs.map(r => num(r.alarm_threshold)),
    alarm: rs.map(r => (r.alarm_flag != null ? !!+r.alarm_flag : +(r.alarm_prob ?? 0) >= 0.5)),
  };
  return { label, series };
}

const def: WidgetDef = {
  id: 'dz-signal-chart',
  reactsTo: ['geography', 'programme', 'period'],
  render(ctx: WidgetCtx) {
    let model = 'ears_c1', showCI = true, showAlarms = true;
    let chartCleanup: (() => void) | undefined;

    ctx.el.innerHTML = `
      <div class="local-controls">
        <span class="lc-label">Detection model</span>
        <select class="dz-sig-model">${MODEL_OPTS.map(m => `<option value="${m.id}">${m.label}</option>`).join('')}</select>
        <span class="lc-spacer"></span>
        <span class="lc-toggle active" data-tog="alarms"><svg class="ico ico-sm"><use href="#i-alert"/></svg> Mark alarms</span>
        <span class="lc-toggle active" data-tog="ci">95% CI band</span>
      </div>
      <div class="dz-sig-mount"></div>
      <div class="dz-sig-cap text-xs muted mt-sm"></div>`;
    const mount = ctx.el.querySelector('.dz-sig-mount') as HTMLElement;
    const cap = ctx.el.querySelector('.dz-sig-cap') as HTMLElement;

    const draw = async () => {
      if (chartCleanup) { chartCleanup(); chartCleanup = undefined; }
      mount.innerHTML = '<p class="muted" style="padding:1rem">Loading…</p>';
      try {
        const rows = await ctx.fetchData({ model: signalModelId(model) });
        mount.innerHTML = '';
        const picked = rows && rows.length ? pickSeries(rows, ctx.filters.programme) : null;
        if (!picked) { mount.innerHTML = '<div class="widget-empty">No signal series for the current scope.</div>'; cap.textContent = ''; return; }
        const N = periodWeeks(ctx.filters.period);
        const s = picked.series;
        const sliced: SignalSeries = {
          weeks: s.weeks.slice(-N), observed: s.observed.slice(-N), predicted: s.predicted.slice(-N),
          lwr: s.lwr.slice(-N), upr: s.upr.slice(-N), threshold: s.threshold.slice(-N), alarm: s.alarm.slice(-N),
        };
        chartCleanup = timeSeriesBand(mount, sliced, ctx.theme, { showCI, showAlarms });
        cap.innerHTML = `Focal disease: <strong>${picked.label}</strong>`;
      } catch {
        // Never leave the "Loading…" placeholder stuck if pickSeries/chart render throws.
        mount.innerHTML = '<div class="widget-empty">Could not load the alarm signal. Please try again.</div>';
        cap.textContent = '';
      }
    };

    (ctx.el.querySelector('.dz-sig-model') as HTMLSelectElement).onchange = (e) => { model = (e.target as HTMLSelectElement).value; draw(); };
    ctx.el.querySelectorAll<HTMLElement>('[data-tog]').forEach(t => {
      t.onclick = () => {
        const on = t.classList.toggle('active');
        if (t.dataset.tog === 'alarms') showAlarms = on; else showCI = on;
        draw();
      };
    });
    draw();
    return () => { if (chartCleanup) chartCleanup(); };
  },
};
export default def;
