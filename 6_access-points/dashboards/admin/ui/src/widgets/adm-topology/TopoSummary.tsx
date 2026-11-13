/**
 * TopoSummary: the at-a-glance header for the live system-topology page.
 *
 * Summary cards and echarts graphs, driven entirely by data the page already
 * fetches (this component calls no endpoint of its own):
 *   • /graph: cluster-health donut, nodes-by-tier breakdown, traffic totals
 *   • /traffic: merged onto graph.edges: active-edge count and aggregate req/s
 *   • /infra: host CPU-load, memory and load gauges
 *   • /cluster: live cluster-capacity card (pods by phase, node usage, HPA)
 *
 * No colours or fonts are hardcoded: chart colours are resolved from the admin
 * design tokens via getComputedStyle, as in renderTopology. Dashboards share no
 * modules, so this echarts glue is local to the admin app.
 */
import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import type { TopoGraph, TopoEdge } from '../../topology/renderTopology';
import type { InfraResp, ClusterResp } from '../../api/topology';

// small formatters for the live cluster-capacity card
function fmtGiB(b: number | null | undefined): string {
  if (b == null) return 'n/a';
  const g = b / 1024 ** 3;
  return g >= 10 ? `${Math.round(g)}` : `${Math.round(g * 10) / 10}`;
}
function fmtCores(mc: number | null | undefined): string {
  if (mc == null) return 'n/a';
  return mc >= 1000 ? `${Math.round((mc / 1000) * 10) / 10}` : `${mc}m`;
}
function pctOf(used: number | null | undefined, total: number | null | undefined): number | null {
  if (used == null || !total) return null;
  return Math.min(100, Math.max(0, Math.round((used / total) * 100)));
}
const barVar = (p: number | null) =>
  p == null ? 'var(--border-2)' : p > 85 ? 'var(--alert)' : p > 65 ? 'var(--watch)' : 'var(--good)';

// hex token to rgba (echarts gradients need concrete colours, not var())
function hexA(hex: string, a: number): string {
  let h = (hex || '').trim().replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  if (h.length !== 6) return `rgba(127,127,127,${a})`;
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
}

// pods-by-phase to ordered, coloured segments (only non-zero shown)
const PHASE_VAR: Record<string, string> = {
  Running: 'var(--good)', Succeeded: 'var(--info)', Pending: 'var(--watch)',
  Failed: 'var(--alert)', Unknown: 'var(--ink-4)',
};
const PHASE_ORDER = ['Running', 'Pending', 'Succeeded', 'Failed', 'Unknown'];

export interface HealthSummary {
  healthy: number; degraded: number; down: number;
  notDeployed: number; unknown: number;
}

interface Tokens {
  good: string; watch: string; alert: string; info: string;
  ink: string; ink2: string; ink3: string; ink4: string;
  border: string; border2: string; paper: string; paper2: string;
  navy700: string; fontSans: string; fontMono: string;
}

function resolveTokens(): Tokens {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    good: v('--good'), watch: v('--watch'), alert: v('--alert'), info: v('--info'),
    ink: v('--ink'), ink2: v('--ink-2'), ink3: v('--ink-3'), ink4: v('--ink-4'),
    border: v('--border'), border2: v('--border-2'), paper: v('--paper'), paper2: v('--paper-2'),
    navy700: v('--navy-700'), fontSans: v('--font-sans'), fontMono: v('--font-mono'),
  };
}

/** Mount an echarts instance into a ref div; re-render on option change; clean up. */
function useChart(optionFactory: () => echarts.EChartsOption | null, deps: unknown[]) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    if (!chart.current) chart.current = echarts.init(ref.current, undefined, { renderer: 'canvas' });
    const opt = optionFactory();
    if (opt) chart.current.setOption(opt, true);
    const onResize = () => chart.current?.resize();
    window.addEventListener('resize', onResize);
    let ro: ResizeObserver | undefined;
    try { ro = new ResizeObserver(() => chart.current?.resize()); ro.observe(ref.current); } catch { /* window resize covers it */ }
    return () => { window.removeEventListener('resize', onResize); ro?.disconnect(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => () => { chart.current?.dispose(); chart.current = null; }, []);
  return ref;
}

const TIER_LABEL: Record<string, string> = {
  dashboard: 'Dashboards', api: 'API / gateway', storage: 'Storage',
  compute: 'Compute', engine: 'Engines', ml: 'ML / serving', serving: 'Serving',
  orchestrator: 'Orchestration', platform: 'Platform', security: 'Security',
  external: 'External', ops: 'Observability', observability: 'Observability',
};
const fmtTier = (t: string) => TIER_LABEL[t] || (t ? t[0].toUpperCase() + t.slice(1) : 'Other');

export default function TopoSummary({ graph, infra, health, cluster }:
  { graph: TopoGraph | null; infra: InfraResp | null; health: HealthSummary; cluster: ClusterResp | null }) {
  const tk = useMemo(resolveTokens, [graph, infra]);

  // derived metrics
  const nodeTotal = graph?.nodes.length ?? 0;
  const deployed = health.healthy + health.degraded + health.down;
  const healthPct = deployed ? Math.round((health.healthy / deployed) * 100) : 0;

  const tiers = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of graph?.nodes ?? []) m.set(n.tier || 'other', (m.get(n.tier || 'other') ?? 0) + 1);
    return [...m.entries()].map(([tier, count]) => ({ tier, count })).sort((a, b) => b.count - a.count);
  }, [graph]);

  // Inbound user traffic only: count the browser requests entering the cluster
  // through the gateway, i.e. the SPA to nhdw-gateway edges
  // (admin/internal-bi/public/workshop to nhdw-gateway). Internal gateway to backend
  // hops (to Trino, the object store, MLflow) are pod-to-pod and excluded.
  const traffic = useMemo(() => {
    const GATEWAY_ID = 'nhdw-gateway';
    const SPA_IDS = new Set(['admin', 'internal-bi', 'public', 'workshop']);
    const spaNodeIds = new Set(
      (graph?.nodes ?? []).filter(n => n.tier === 'dashboard' || SPA_IDS.has(n.id)).map(n => n.id),
    );
    const isInbound = (e: TopoEdge) => e.target === GATEWAY_ID && spaNodeIds.has(e.source);
    const edges = (graph?.edges ?? []).filter(isInbound);
    const active = edges.filter(e => e.traffic && (e.traffic.reqRate ?? 0) > 0);
    const aggReq = active.reduce((s, e) => s + (e.traffic!.reqRate ?? 0), 0);
    const top = [...active].sort((a, b) => (b.traffic!.reqRate ?? 0) - (a.traffic!.reqRate ?? 0)).slice(0, 6);
    const errEdges = edges.filter(e => e.traffic && (e.traffic.errRate ?? 0) > 0.01).length;
    // aggregate req/s sparkline: sum the per-edge series element-wise over the
    // window (from ops_audit.gateway_log). No series gives an empty state.
    let len = 0;
    for (const e of edges) if (e.traffic?.series?.length) len = Math.max(len, e.traffic.series.length);
    const series = len ? new Array(len).fill(0) : [];
    for (const e of edges) {
      const s = e.traffic?.series;
      if (!s) continue;
      for (let i = 0; i < s.length; i++) series[len - s.length + i] += s[i] || 0;
    }
    const hasSeries = series.some(v => v > 0);
    return { total: edges.length, active: active.length, aggReq, top, errEdges, series, hasSeries };
  }, [graph]);

  const host = infra?.nodes?.[0];
  const cores = host?.cores ?? 0;
  const load1 = host?.load_1m ?? 0;
  const cpuPct = cores ? Math.min(100, Math.round((load1 / cores) * 100)) : null;
  const memTot = host?.memory_total_kib ?? 0;
  const memUsed = memTot - (host?.memory_avail_kib ?? 0);
  const memPct = memTot ? Math.min(100, Math.round((memUsed / memTot) * 100)) : null;
  const gbUsed = memTot ? Math.round((memUsed / 1024 / 1024) * 10) / 10 : 0;
  const gbTot = memTot ? Math.round((memTot / 1024 / 1024) * 10) / 10 : 0;

  // charts
  const healthRef = useChart((): echarts.EChartsOption | null => {
    if (!graph) return null;
    const raw = [
      { name: 'Healthy',       value: health.healthy,     color: tk.good },
      { name: 'Degraded',      value: health.degraded,    color: tk.watch },
      { name: 'Down',          value: health.down,        color: tk.alert },
      { name: 'Not deployed',  value: health.notDeployed, color: tk.border2 },
      { name: 'Unknown',       value: health.unknown,     color: tk.ink4 },
    ].filter(d => d.value > 0);
    return {
      textStyle: { fontFamily: tk.fontSans },
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie', radius: ['62%', '86%'], center: ['50%', '50%'], avoidLabelOverlap: false,
        itemStyle: { borderColor: tk.paper, borderWidth: 2, borderRadius: 3 },
        label: {
          show: true, position: 'center',
          formatter: () => `{v|${health.healthy}/${deployed || nodeTotal}}\n{l|healthy}`,
          rich: {
            v: { fontSize: 24, fontWeight: 700, color: tk.ink, fontFamily: tk.fontSans, lineHeight: 26 },
            l: { fontSize: 10, color: tk.ink3, fontFamily: tk.fontSans, padding: [2, 0, 0, 0] },
          },
        },
        labelLine: { show: false },
        data: raw.map(d => ({ name: d.name, value: d.value, itemStyle: { color: d.color } })),
      }],
    };
  }, [graph, tk, health.healthy, health.degraded, health.down, health.notDeployed, health.unknown]);

  const gauge = (value: number | null, max: number, label: string, sub: string, color: string): echarts.EChartsOption => ({
    textStyle: { fontFamily: tk.fontSans },
    series: [{
      type: 'gauge', radius: '96%', center: ['50%', '62%'], startAngle: 210, endAngle: -30,
      min: 0, max, splitNumber: 4, progress: { show: true, width: 7, itemStyle: { color } },
      axisLine: { lineStyle: { width: 7, color: [[1, tk.border]] } },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      pointer: { show: false }, anchor: { show: false },
      title: { offsetCenter: [0, '40%'], color: tk.ink4, fontSize: 9, fontWeight: 600 },
      detail: {
        offsetCenter: [0, '-2%'], color: tk.ink, fontSize: 18, fontWeight: 700,
        formatter: () => (value == null ? 'n/a' : sub),
      },
      data: [{ value: value ?? 0, name: label }],
    }],
  });
  const cpuRef = useChart(() => (infra ? gauge(cpuPct, 100, 'CPU LOAD', cpuPct == null ? 'n/a' : `${cpuPct}%`, tk.info) : null), [infra, tk, cpuPct]);
  const memRef = useChart(() => (infra ? gauge(memPct, 100, 'MEMORY', memPct == null ? 'n/a' : `${memPct}%`, memPct != null && memPct > 85 ? tk.alert : memPct != null && memPct > 65 ? tk.watch : tk.good) : null), [infra, tk, memPct]);
  const loadRef = useChart(() => (infra ? gauge(load1, Math.max(cores || 1, Math.ceil(load1) || 1), 'LOAD 1m', load1 ? load1.toFixed(2) : 'n/a', load1 > (cores || 1) ? tk.watch : tk.good) : null), [infra, tk, load1, cores]);

  const tierRef = useChart((): echarts.EChartsOption | null => {
    if (!tiers.length) return null;
    const cats = tiers.map(t => fmtTier(t.tier)).reverse();
    const vals = tiers.map(t => t.count).reverse();
    return {
      textStyle: { fontFamily: tk.fontSans },
      grid: { left: 4, right: 22, top: 6, bottom: 2, containLabel: true },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: tk.border } }, axisLabel: { color: tk.ink4, fontSize: 10 }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: 'category', data: cats, axisLabel: { color: tk.ink2, fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
      series: [{
        type: 'bar', data: vals, barWidth: '58%',
        itemStyle: { color: tk.navy700, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: tk.ink3, fontSize: 11, fontWeight: 600 },
      }],
    };
  }, [tiers, tk]);

  // Aggregate req/s sparkline over the window (from the gateway_log per-bucket
  // series).
  const trafficRef = useChart((): echarts.EChartsOption | null => {
    if (!traffic.hasSeries) return null;
    const data = traffic.series.map(v => Math.round(v * 1000) / 1000);
    const errTone = traffic.errEdges > 0 ? tk.watch : tk.good;
    return {
      textStyle: { fontFamily: tk.fontSans },
      grid: { left: 6, right: 8, top: 10, bottom: 4, containLabel: false },
      tooltip: { trigger: 'axis', axisPointer: { type: 'line' }, valueFormatter: (v: any) => `${v} req/s` },
      xAxis: { type: 'category', boundaryGap: false, show: false, data: data.map((_, i) => `${i}`) },
      yAxis: { type: 'value', show: false, min: 0 },
      series: [{
        type: 'line', data, smooth: true, symbol: 'none',
        lineStyle: { color: errTone, width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: hexA(errTone, 0.28) }, { offset: 1, color: hexA(errTone, 0.02) },
          ]),
        },
      }],
    };
  }, [traffic, tk]);

  const podTotal = cluster?.podTotal ?? 0;
  const livePhases = cluster ? PHASE_ORDER.filter(p => (cluster.podsByPhase[p] ?? 0) > 0) : [];

  return (
    <div className="topo-summary">
      <div className="topo-sum-card sp2">
        <div className="topo-sum-hd" title="In-cluster Kubernetes workloads only. External dependencies are not nodes.">
          <span>Cluster health</span><span className="topo-sum-meta">{deployed || nodeTotal} in-cluster workloads</span>
        </div>
        <div ref={healthRef} className="topo-sum-chart" style={{ height: 150 }} />
        <div className="topo-sum-foot">
          <span className="topo-sum-leg"><i style={{ background: tk.good }} />{health.healthy} up</span>
          {health.degraded > 0 && <span className="topo-sum-leg"><i style={{ background: tk.watch }} />{health.degraded} deg</span>}
          {health.down > 0 && <span className="topo-sum-leg"><i style={{ background: tk.alert }} />{health.down} down</span>}
          {health.notDeployed > 0 && <span className="topo-sum-leg"><i style={{ background: tk.border2 }} />{health.notDeployed} n/d</span>}
          {health.unknown > 0 && <span className="topo-sum-leg"><i style={{ background: tk.ink4 }} />{health.unknown} unk</span>}
        </div>
      </div>

      <div className="topo-sum-card sp2">
        <div className="topo-sum-hd"><span>Host resources</span>
          <span className="topo-sum-meta">{host?.hostname ? host.hostname : cores ? `${cores} cores` : 'metrics'}</span>
        </div>
        {host && !host.error ? (
          <div className="topo-gauge-row">
            <div ref={cpuRef} className="topo-gauge" />
            <div ref={memRef} className="topo-gauge" />
            <div ref={loadRef} className="topo-gauge" />
          </div>
        ) : (
          <p className="topo-sum-empty">{host?.error ? `Host metrics unavailable: ${host.error}` : 'Host metrics loading…'}</p>
        )}
        {host && !host.error && (
          <div className="topo-sum-foot">
            <span className="topo-sum-leg2">mem <strong>{gbUsed}</strong> / {gbTot} GB</span>
            <span className="topo-sum-leg2">load <strong>{load1 ? load1.toFixed(2) : 'n/a'}</strong> · {cores || 'n/a'} cores</span>
          </div>
        )}
      </div>

      <div className="topo-sum-card sp2">
        <div className="topo-sum-hd" title="User/browser requests entering the cluster through the gateway. Internal pod-to-pod hops are excluded.">
          <span>Inbound traffic <span className="topo-sum-meta" style={{ fontWeight: 500 }}>· req/s</span></span>
          <span className="topo-sum-meta">{graph?.meta?.window ? `window ${graph.meta.window}` : 'live'}</span>
        </div>
        <div className="topo-sum-subcap" style={{ fontSize: '0.64rem', color: 'var(--ink-4)', margin: '-0.15rem 0 0.5rem', lineHeight: 1.4 }}>
          User-to-cluster requests entering via the gateway (external to cluster). Internal pod-to-pod hops excluded.
        </div>
        <div className="topo-sum-kpis">
          <div className="topo-sum-kpi"><div className="v" style={{ color: traffic.active ? tk.good : tk.ink3 }}>{traffic.active}</div><div className="l">inbound edges</div></div>
          <div className="topo-sum-kpi"><div className="v">{traffic.aggReq ? traffic.aggReq.toFixed(2) : '0'}</div><div className="l">req/s now</div></div>
          <div className="topo-sum-kpi"><div className="v" style={{ color: traffic.errEdges ? tk.watch : tk.ink3 }}>{traffic.errEdges}</div><div className="l">edges w/ err</div></div>
        </div>
        {traffic.hasSeries ? <div ref={trafficRef} className="topo-sum-chart" style={{ height: 96 }} />
          : <p className="topo-sum-empty">No inbound user traffic in this window. The gateway audit log
            (<code>ops_audit.gateway_log</code>) is the only real source on this mesh-less cluster. Open a dashboard to see it move.</p>}
      </div>

      <div className="topo-sum-card sp2">
        <div className="topo-sum-hd" title="Running Kubernetes workloads grouped by their tier label.">
          <span>Workloads by tier</span><span className="topo-sum-meta">{nodeTotal} nodes</span>
        </div>
        <div className="topo-sum-subcap" style={{ fontSize: '0.64rem', color: 'var(--ink-4)', margin: '-0.15rem 0 0.35rem', lineHeight: 1.4 }}>
          Running k8s workloads grouped by their <code>tier</code> label (data · platform · gateway · access …).
        </div>
        {tiers.length ? <div ref={tierRef} className="topo-sum-chart" style={{ height: 168 }} />
          : <p className="topo-sum-empty">No nodes.</p>}
      </div>

      {/* live cluster capacity: pods-by-phase + node usage + HPA (from the k8s
          API and metrics-server) */}
      <div className="topo-sum-card sp4">
        <div className="topo-sum-hd"><span>Cluster capacity</span>
          <span className="topo-sum-meta">{cluster ? `${podTotal} pods · live` : 'loading…'}</span>
        </div>
        {!cluster ? (
          <p className="topo-sum-empty">Cluster metrics loading…</p>
        ) : (
          <div className="topo-cluster">
            <div className="topo-cluster-col">
              <div className="topo-cluster-lbl">Pods by phase</div>
              <div className="topo-cluster-total">{podTotal}<span>pods</span></div>
              <div className="topo-phasebar">
                {livePhases.map(p => (
                  <span key={p} className="topo-phaseseg"
                        style={{ width: `${podTotal ? ((cluster.podsByPhase[p] ?? 0) / podTotal) * 100 : 0}%`, background: PHASE_VAR[p] }}
                        title={`${p}: ${cluster.podsByPhase[p]}`} />
                ))}
              </div>
              <div className="topo-phaselegend">
                {livePhases.map(p => (
                  <span key={p} className="topo-phase-leg"><i style={{ background: PHASE_VAR[p] }} />{cluster.podsByPhase[p]} {p.toLowerCase()}</span>
                ))}
              </div>
            </div>

            <div className="topo-cluster-col">
              <div className="topo-cluster-lbl">Node usage{cluster.haveNodeMetrics ? '' : ' · metrics n/a'}</div>
              {cluster.nodes.length ? cluster.nodes.map(nd => {
                const cpuP = pctOf(nd.cpu.usedMillicores, nd.cpu.allocatableMillicores);
                const memP = pctOf(nd.memory.usedBytes, nd.memory.allocatableBytes);
                return (
                  <div key={nd.name} className="topo-node-usage">
                    <div className="topo-node-name" title={nd.name}>{nd.name}</div>
                    <div className="topo-node-metric">
                      <span className="topo-node-mlbl">CPU</span>
                      <div className="topo-bar sm"><div className="topo-bar-fill" style={{ width: `${cpuP ?? 0}%`, background: barVar(cpuP) }} /></div>
                      <span className="topo-node-mval">{cpuP != null ? `${cpuP}%` : 'n/a'}</span>
                    </div>
                    <div className="topo-node-metric">
                      <span className="topo-node-mlbl">Mem</span>
                      <div className="topo-bar sm"><div className="topo-bar-fill" style={{ width: `${memP ?? 0}%`, background: barVar(memP) }} /></div>
                      <span className="topo-node-mval">{memP != null ? `${memP}%` : 'n/a'}</span>
                    </div>
                    <div className="topo-node-sub">{fmtCores(nd.cpu.usedMillicores)} / {fmtCores(nd.cpu.allocatableMillicores)} cores · {fmtGiB(nd.memory.usedBytes)} / {fmtGiB(nd.memory.allocatableBytes)} GiB</div>
                  </div>
                );
              }) : <p className="topo-sum-empty">No node metrics.</p>}
            </div>

            <div className="topo-cluster-col">
              <div className="topo-cluster-lbl">Autoscalers · HPA</div>
              {cluster.hpas.length ? (
                <div className="topo-hpa-list">
                  {cluster.hpas.map(h => (
                    <div key={h.name} className="topo-hpa">
                      <span className="topo-hpa-name" title={h.name}>{h.target ?? h.name}</span>
                      <span className="topo-hpa-rep"><strong>{h.current ?? 'n/a'}</strong>→{h.desired ?? 'n/a'}<em> / {h.min ?? '?'} to {h.max ?? '?'}</em></span>
                      <span className="topo-hpa-cpu">{h.currentCpuPct != null ? `${h.currentCpuPct}%` : 'n/a'}{h.targetCpuPct != null ? ` / ${h.targetCpuPct}%` : ''}</span>
                    </div>
                  ))}
                </div>
              ) : <p className="topo-cluster-none">No HPAs. Replicas are fixed. Scale live from a node's detail panel.</p>}
              <p className="topo-sum-note">RED (req / err / latency) + history need the observability stack.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
