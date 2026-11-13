/** ws-model-workbench: the workshop Model "card + how to use" panel.
 *  reactsTo `ws_model` (published by ws-models-table); instructive empty
 *  state until a model is selected.
 *
 *  Usage only: a publicly available model exposes only what a caller needs to
 *  use it, never stored prediction results and never real-time
 *  health/drift/performance (those are admin-only). The widget shows minimal
 *  model info, a few catalogue metrics, the model's input and output schema and
 *  a typed form to run the model on the user's own input, with the result shown
 *  once (never persisted to a list).
 *
 *   GET  /gw/ml/{model}            facts: aliases, deployment, registry
 *                                  description (the package model-card text)
 *   GET  /gw/ml/{model}/versions   registry versions (aliases merged from the
 *                                  detail payload: the versions route's
 *                                  per-version aliases are empty)
 *   GET  /gw/ml/{model}/schema     input/output signature + input_example
 *                                  (catalogue metadata; no stored predictions)
 *   POST /gw/ml/{model}/predict    async ad-hoc predict on the user's row:
 *   GET  /gw/ml/jobs/{id}          poll (~10-30 s) for the presigned result CSV
 *   GET  /gw/ml/{model}/runs       the runner's VERBATIM error after a failed
 *                                  Job (the k8s condition only says "backoff
 *                                  limit reached")
 *
 *  The stored-batch-sample section and the /predictions read were REMOVED here
 *  (privacy + security): a public model must not surface other users' results. */
import type { WidgetDef, WidgetCtx } from '../../runtime/types';
import {
  getModel, listVersions, getSchema, submitPredict, getJob, listRuns,
  fmtEpochMs, fmtDateTime, fmtNum, aliasPillClass,
  type MlModelDetail, type MlVersion, type MlSchema, type MlJobStatus,
} from '../../api/ml';
import { esc } from '../_shared/helpers';

const NUMERIC_RE = /^(long|integer|int|double|float|decimal|number)$/i;

function fact(icon: string, label: string, valueHtml: string): string {
  return `
    <div class="mdl-fact">
      <svg class="ico"><use href="#${icon}"/></svg>
      <div>
        <div class="mdl-fact-lbl">${esc(label)}</div>
        <div class="mdl-fact-val">${valueHtml}</div>
      </div>
    </div>`;
}

/** Friendly one-word type for a signature field. */
function typeLabel(t: string | null | undefined): string {
  if (!t) return '';
  const s = String(t).toLowerCase();
  if (NUMERIC_RE.test(s)) return s === 'double' || s === 'float' ? 'number' : 'integer';
  if (s === 'datetime' || s === 'date') return 'date';
  if (s === 'boolean' || s === 'bool') return 'boolean';
  return 'text';
}

function versionsHtml(versions: MlVersion[] | null, err: string | null,
                      modelAliases: Record<string, string>): string {
  if (err) return `<p class="mdl-note alert">Failed to load versions: ${esc(err)}</p>`;
  if (versions === null) return `<p class="muted">Loading versions…</p>`;
  if (!versions.length) return `<p class="mdl-note">No versions registered yet.</p>`;
  const aliasesFor = (version: string): string[] => {
    const fromDetail = Object.entries(modelAliases)
      .filter(([, v]) => v === version).map(([a]) => a);
    return [...new Set([...fromDetail, ...(versions.find(x => x.version === version)?.aliases ?? [])])];
  };
  return `
    <div class="art-schema mdl-scroll-x">
      <table>
        <thead><tr><th>Version</th><th>Alias</th><th>Registered</th><th>MLflow run</th></tr></thead>
        <tbody>
          ${versions.map(v => {
            const aliases = aliasesFor(v.version);
            return `
            <tr>
              <td><code>v${esc(v.version)}</code></td>
              <td>${aliases.length
                ? aliases.map(a => `<span class="pill ${esc(aliasPillClass(a))}">@${esc(a)}</span>`).join(' ')
                : '<span class="muted">n/a</span>'}</td>
              <td>${esc(fmtEpochMs(v.creation_timestamp))}</td>
              <td>${v.run_id ? `<code title="${esc(v.run_id)}">${esc(v.run_id.slice(0, 12))}…</code>` : 'n/a'}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
}

/** Output schema: the SHAPE the model returns (names + types), never values. */
function outputSchemaHtml(schema: MlSchema | null, err: string | null): string {
  if (err) return `<p class="mdl-note alert">Failed to load schema: ${esc(err)}</p>`;
  if (schema === null) return `<p class="muted">Loading schema…</p>`;
  if (!schema.outputs.length) {
    return `<p class="mdl-note">The output schema is not published for this model.</p>`;
  }
  return `
    <div class="art-schema mdl-scroll-x">
      <table>
        <thead><tr><th>Field</th><th>Type</th></tr></thead>
        <tbody>
          ${schema.outputs.map(o => `
            <tr>
              <td><code>${esc(o.name)}</code></td>
              <td><span class="mdl-field-type">${esc(typeLabel(o.type))}</span>
                  ${o.type ? `<span class="muted"> · ${esc(o.type)}</span>` : ''}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

/* the typed run form (input schema, fill, run, result shown once) */

type RunPhase = 'idle' | 'submitting' | 'running' | 'succeeded' | 'failed';

const FLOAT_RE = /^(double|float|decimal|number)$/i;

/** Serialize a field value into a CSV-safe cell for the runner. Everything
 *  travels as text (the gateway builds a CSV the runner reads with pandas, then
 *  MLflow enforces the signature). For double/float columns we must keep a
 *  decimal point even for whole numbers ("71" to "71.0"): otherwise pandas
 *  infers int64 and MLflow refuses the int64 to float64 coercion. */
function toCsvCell(v: string, type: string | null | undefined): string {
  const s = v.trim();
  if (!s) return s;
  if (type && FLOAT_RE.test(String(type))) {
    const n = Number(s);
    if (Number.isFinite(n)) return Number.isInteger(n) ? n.toFixed(1) : String(n);
  }
  return s;
}

/** Naive CSV split: the runner's output columns hold no embedded commas for
 *  the seeded forecast schemas; the download link remains authoritative. */
function parseCsv(text: string): { cols: string[]; rows: string[][] } {
  const lines = text.replace(/﻿/g, '').trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length) return { cols: [], rows: [] };
  const cols = lines[0].split(',');
  const rows = lines.slice(1, 9).map(l => l.split(','));
  return { cols, rows };
}

function mountRunForm(el: HTMLElement, model: string, schema: MlSchema): () => void {
  const values: Record<string, string> = {};
  const state = {
    phase: 'idle' as RunPhase,
    jobId: null as string | null,
    job: null as MlJobStatus | null,
    error: null as string | null,
    resultHtml: null as string | null,
    elapsed: 0,
    start: 0,
  };
  let pollIv: number | undefined;
  let tickIv: number | undefined;
  let disposed = false;

  const stopTimers = () => {
    if (pollIv) { window.clearInterval(pollIv); pollIv = undefined; }
    if (tickIv) { window.clearInterval(tickIv); tickIv = undefined; }
  };

  const buildRow = (): Record<string, unknown> => {
    const row: Record<string, unknown> = {};
    for (const f of schema.inputs) {
      const raw = (values[f.name] ?? '').trim();
      const chosen = raw !== '' ? raw
        : (f.example != null ? String(f.example) : '');
      row[f.name] = toCsvCell(chosen, f.type);
    }
    return row;
  };

  /** Best-effort inline preview of the run's own result: fetched once from the
   *  presigned URL, never stored. Falls back to the download link on any error
   *  (e.g. CORS). */
  const loadResultPreview = async (url: string): Promise<void> => {
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(String(r.status));
      const text = await r.text();
      const { cols, rows } = parseCsv(text);
      if (!cols.length || !rows.length) return;
      state.resultHtml = `
        <div class="mdl-run-out">
          <div class="art-schema mdl-scroll-x">
            <table>
              <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
              <tbody>
                ${rows.map(rw => `<tr>${cols.map((_, i) =>
                  `<td><code>${esc(rw[i] ?? '')}</code></td>`).join('')}</tr>`).join('')}
              </tbody>
            </table>
          </div>
          <p class="mdl-run-out-note">Showing your run's first ${rows.length} output row(s).
            This result is shown once and is <strong>not saved</strong> to any list.</p>
        </div>`;
      if (!disposed && state.phase === 'succeeded') paint();
    } catch { /* keep the download-only rendering */ }
  };

  const startPolling = () => {
    pollIv = window.setInterval(async () => {
      if (state.phase !== 'running' || !state.jobId) return;
      try {
        const j = await getJob(state.jobId);
        if (disposed) return;
        state.job = j;
        if (j.status === 'succeeded') {
          state.phase = 'succeeded';
          stopTimers();
          paint();
          if (j.result_url) void loadResultPreview(j.result_url);
        } else if (j.status === 'failed') {
          // The Job condition only carries the generic "backoff limit" text;
          // the runner's real error (e.g. the exact signature mismatch) is in
          // ops_ml.ml_run.error_msg: fetch the matching ad-hoc run + surface it.
          let msg = j.error || 'runner job failed (no error message reported)';
          try {
            const runs = await listRuns(model, 10);
            const detailed = runs.find(r =>
              r.trigger_kind === 'api' && r.status === 'failed' && !!r.error_msg &&
              !!r.started_at &&
              new Date(r.started_at).getTime() >= state.start - 120_000);
            if (detailed?.error_msg) msg = detailed.error_msg;
          } catch { /* keep the Job condition message */ }
          if (disposed) return;
          state.error = msg;
          state.phase = 'failed';
          stopTimers();
          paint();
        }
      } catch { /* transient: next tick retries */ }
    }, 2500);
    tickIv = window.setInterval(() => {
      state.elapsed = Math.round((Date.now() - state.start) / 1000);
      const t = el.querySelector<HTMLElement>('.mdl-run-elapsed');
      if (t) t.textContent = `${state.elapsed}s`;
    }, 1000);
  };

  const run = async () => {
    state.error = null;
    state.job = null;
    state.jobId = null;
    state.resultHtml = null;
    state.start = Date.now();
    state.elapsed = 0;
    state.phase = 'submitting';
    paint();
    try {
      const sub = await submitPredict(model, { rows: [buildRow()] });
      if (disposed) return;
      state.jobId = sub.job_id;
      state.phase = 'running';
      paint();
      startPolling();
    } catch (e: any) {
      if (disposed) return;
      state.error = e?.message || String(e);
      state.phase = 'failed';
      stopTimers();
      paint();
    }
  };

  const reset = () => {
    stopTimers();
    state.phase = 'idle';
    state.jobId = null;
    state.job = null;
    state.error = null;
    state.resultHtml = null;
    state.elapsed = 0;
    paint();
  };

  function formFieldsHtml(): string {
    if (!schema.inputs.length) {
      return `<p class="mdl-note">The input schema is not published for this model,
        so a typed run form isn't available.</p>`;
    }
    return `
      <div class="mdl-form">
        ${schema.inputs.map(f => `
          <label class="mdl-field">
            <span class="mdl-field-lbl">
              <code>${esc(f.name)}</code>
              <span class="mdl-field-type">${esc(typeLabel(f.type))}</span>
              ${f.required ? '<span class="mdl-field-req" title="required">*</span>' : ''}
            </span>
            <input type="text" data-field="${esc(f.name)}"
              value="${esc(values[f.name] ?? '')}"
              placeholder="${f.example != null ? esc(String(f.example)) : ''}"
              autocomplete="off" spellcheck="false" />
          </label>`).join('')}
      </div>`;
  }

  function paint() {
    let body: string;
    if (state.phase === 'idle' || state.phase === 'failed') {
      body = `
        ${formFieldsHtml()}
        ${state.phase === 'failed' && state.error ? `
          <div class="alert-banner" style="background:var(--alert-50);color:var(--alert)">
            <div style="min-width:0;flex:1">
              <div>Prediction failed${state.jobId ? `. Runner job <code>${esc(state.jobId)}</code> reported:` : ':'}</div>
              <pre class="mdl-run-err">${esc(state.error)}</pre>
            </div>
          </div>` : ''}
        <div class="mdl-run-actions">
          <button class="btn btn-primary" data-act="run"${schema.inputs.length ? '' : ' disabled'}>
            <svg class="ico"><use href="#i-play"/></svg>
            ${state.phase === 'failed' ? 'Try again' : 'Run on these inputs'}</button>
        </div>`;
    } else if (state.phase === 'succeeded' && state.job) {
      const job = state.job;
      body = `
        <div class="mdl-run-done">
          <p><span class="pill good">succeeded</span>
            Runner job <code>${esc(job.job_id)}</code> finished${job.completion_time ? ` at ${esc(fmtDateTime(job.completion_time))}` : ''}
            (${state.elapsed}s from submit).</p>
          ${state.resultHtml ?? ''}
          ${job.result_url
            ? `<a class="btn btn-primary" href="${esc(job.result_url)}">
                 <svg class="ico"><use href="#i-download"/></svg> Download result CSV</a>`
            : `<div class="alert-banner" style="background:var(--watch-50);color:var(--watch)">
                 The result CSV is stored at <code>${esc(job.result_uri || 'unknown')}</code>, but a
                 browser download link could not be signed; ask an operator to fetch it.</div>`}
          <div class="mdl-run-actions">
            <button class="btn btn-ghost btn-sm" data-act="reset">Run again</button>
          </div>
        </div>`;
    } else {
      body = `
        <div class="mdl-run-progress" role="status">
          <span class="mdl-spinner" aria-hidden="true"></span>
          <div>
            <strong>${state.phase === 'submitting'
              ? 'Submitting your inputs and creating the runner job…'
              : 'Model running in the cluster…'}</strong>
            <div class="muted">
              ${state.jobId ? `job <code>${esc(state.jobId)}</code> · ` : ''}
              <span class="mdl-run-elapsed">${state.elapsed}s</span> elapsed, typically 10 to 30s.
              The job keeps running if you navigate away.</div>
          </div>
        </div>`;
    }

    el.innerHTML = `
      <div class="mdl-run">
        <div class="mdl-run-hdr">
          <svg class="ico"><use href="#i-play"/></svg>
          <h4>Run this model on your inputs</h4>
        </div>
        <p class="mdl-note">
          Fill the fields below (they are pre-filled with example values as
          placeholders) and submit. This creates a one-off runner job that loads
          <code>${esc(model)}@champion</code>, scores your row and returns a
          result CSV. <strong>This takes roughly 10 to 30 seconds.</strong>
          Your result is shown once here and is never saved to a shared list.</p>
        ${body}
      </div>`;

    // wire events for the current phase
    el.querySelectorAll<HTMLInputElement>('input[data-field]').forEach(inp =>
      inp.addEventListener('input', () => { values[inp.dataset.field!] = inp.value; }));
    el.querySelector<HTMLButtonElement>('button[data-act="run"]')
      ?.addEventListener('click', () => { void run(); });
    el.querySelectorAll<HTMLButtonElement>('button[data-act="reset"]').forEach(b =>
      b.addEventListener('click', reset));
  }

  paint();
  return () => { disposed = true; stopTimers(); };
}

/* the widget */

async function draw(ctx: WidgetCtx): Promise<void | (() => void)> {
  const model = ctx.filters.ws_model || '';
  if (!model) {
    ctx.empty('Select a model in the catalogue above to open its card: model info, evaluation metrics, the input/output schema and a form to run it on your own input.');
    return;
  }

  let detail: MlModelDetail | null = null;
  let detailErr = '';
  let versions: MlVersion[] | null = null;
  let versionsErr: string | null = null;
  let schema: MlSchema | null = null;
  let schemaErr: string | null = null;
  const [dRes, vRes, sRes] = await Promise.allSettled([
    getModel(model),
    listVersions(model),
    getSchema(model),
  ]);
  if (dRes.status === 'fulfilled') detail = dRes.value; else detailErr = dRes.reason?.message || String(dRes.reason);
  if (vRes.status === 'fulfilled') versions = vRes.value; else versionsErr = vRes.reason?.message || String(vRes.reason);
  if (sRes.status === 'fulfilled') schema = sRes.value; else schemaErr = sRes.reason?.message || String(sRes.reason);

  if (!detail) {
    ctx.empty(`Failed to load model “${model}”: ${detailErr}`);
    return;
  }

  const aliases = Object.entries(detail.aliases ?? {});

  // Headline metrics: catalogue metadata only (no stored predictions,
  // no health/drift/performance).
  const inputCount = schema ? schema.inputs.length : null;
  const outputCount = schema ? schema.outputs.length : null;

  ctx.el.innerHTML = `
    <div class="mdl-detail" style="position:static;max-height:none;border:none;box-shadow:none;padding:0">
      <div class="mdl-detail-badges">
        ${aliases.length
          ? aliases.map(([a, v]) => `<span class="pill ${esc(aliasPillClass(a))}">@${esc(a)} v${esc(v)}</span>`).join(' ')
          : '<span class="pill neutral">no alias set</span>'}
        <span class="pill neutral">${esc(detail.deployment_kind)}</span>
        ${detail.enabled === false ? '<span class="pill neutral">paused</span>' : ''}
      </div>
      <h3 class="mdl-detail-name">${esc(detail.model)}</h3>

      ${detail.description
        ? `<p class="mdl-detail-desc">${esc(detail.description)}</p>`
        : `<p class="mdl-note">This model has no description in the registry; its package manifest carried none.</p>`}

      <div class="mdl-facts">
        ${fact('i-shield-check', 'Serving version', detail.champion_version
          ? `v${esc(detail.champion_version)} <span class="muted">via @${esc(detail.deployment?.model_alias || 'champion')}</span>`
          : 'none promoted')}
        ${fact('i-package', 'Task type', `<code>${esc(detail.deployment_kind)}</code>`)}
        ${fact('i-calendar', 'Schedule', detail.schedule
          ? `weekly batch · <code>${esc(detail.schedule)}</code>`
          : detail.deployment_kind === 'batch' ? 'batch (no schedule row)' : 'on demand')}
        ${fact('i-clock', 'Registry updated',
          `${esc(fmtEpochMs(detail.last_updated))}${detail.latest_version ? ` <span class="muted">· latest v${esc(detail.latest_version)}</span>` : ''}`)}
        ${fact('i-list', 'Registered versions',
          versions ? `${esc(fmtNum(versions.length, 0))}` : '<span class="muted">n/a</span>')}
        ${fact('i-database', 'Schema fields',
          (inputCount != null && outputCount != null)
            ? `${esc(String(inputCount))} in · ${esc(String(outputCount))} out`
            : '<span class="muted">n/a</span>')}
      </div>

      <div class="mdl-section">
        <h4 class="mdl-section-h">Versions</h4>
        ${versionsHtml(versions, versionsErr, detail.aliases ?? {})}
      </div>

      <div class="mdl-section">
        <h4 class="mdl-section-h">Input schema</h4>
        <p class="mdl-note">The fields this model expects. Fill them in the run
          form below: example values are shown as placeholders.</p>
        <div class="ws-runform-slot"></div>
      </div>

      <div class="mdl-section">
        <h4 class="mdl-section-h">Output schema</h4>
        <p class="mdl-note">The shape the model returns for each scored row
          (field names and types: no stored values).</p>
        ${outputSchemaHtml(schema, schemaErr)}
      </div>
    </div>`;

  let runnerCleanup: (() => void) | null = null;
  const slot = ctx.el.querySelector<HTMLElement>('.ws-runform-slot')!;
  if (schema && !schemaErr) {
    runnerCleanup = mountRunForm(slot, model, schema);
  } else {
    slot.innerHTML = `<p class="mdl-note alert">The input/output schema could not
      be loaded${schemaErr ? `: ${esc(schemaErr)}` : ''}. A run form is unavailable
      until the schema is published.</p>`;
  }
  return () => { runnerCleanup?.(); };
}

const def: WidgetDef = {
  id: 'ws-model-workbench',
  reactsTo: ['ws_model'],
  render(ctx) { return draw(ctx); },
};
export default def;
