/**
 * Fetch wrappers for /gw/topology/*, the admin system-topology widget's
 * data source.
 *
 *   /graph    apps-only node-link graph {nodes, edges, as_of, meta}, derived
 *             live; edges carry an optional traffic block from the provider
 *             registry (gateway_log). Primary widget feed.
 *   /traffic  windowed per-edge RED map {edge_id: block} from
 *             ops_audit.gateway_log, merged onto the graph edges for
 *             freshness and the edge drill-down panel.
 *   /infra    host capacity (cores/mem/load) for the compact capacity strip.
 *   /node     per-node detail enrichment on click.
 *
 * The graph data contract (Topo*) is owned by src/topology/renderTopology.ts so
 * the renderer stays framework-agnostic; this layer conforms to it through a
 * type-only import, which is erased at build and adds no Cytoscape coupling.
 */
import { authFetch } from './useAuth';
import type { TopoGraph, TopoTraffic } from '../topology/renderTopology';

export type { TopoGraph, TopoTraffic, TopoNode, TopoEdge, TopoHealth } from '../topology/renderTopology';

const GW = '/gw/topology';

async function _json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await authFetch(`${GW}${path}`, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${body}`);
  }
  return r.json();
}

export type InfraNode = {
  id: string;
  kind: 'host' | string;
  hostname?: string;
  cores?: number;
  memory_total_kib?: number;
  memory_avail_kib?: number;
  load_1m?: number; load_5m?: number; load_15m?: number;
  error?: string;
};

export type InfraResp = {
  nodes: InfraNode[];
  edges: any[];
  as_of: string;
};

/** /traffic response: `edges` is a map keyed by edge id. */
export type TrafficResp = {
  window: string;
  window_seconds: number;
  edges: Record<string, TopoTraffic>;
  as_of: string;
};

/** /cluster: live cluster health (pods-by-phase, node usage, HPA). */
export type ClusterNode = {
  name: string;
  cpu: { usedMillicores: number | null; allocatableMillicores: number | null };
  memory: { usedBytes: number | null; allocatableBytes: number | null };
};
export type ClusterHpa = {
  name: string; target: string | null;
  current: number | null; desired: number | null;
  min: number | null; max: number | null;
  currentCpuPct: number | null; targetCpuPct: number | null;
};
export type ClusterResp = {
  podsByPhase: Record<string, number>;
  podTotal: number;
  nodes: ClusterNode[];
  hpas: ClusterHpa[];
  haveNodeMetrics: boolean;
  source: string;
  as_of: string;
};

/** /logs: kubelet-tailed container log for a node's workload + its containers. */
export type NodeLogs = {
  id: string;
  pod: string | null;
  containers: string[];
  container: string | null;
  tail: number;
  lines: string[];
  note?: string;
};

export const getInfra      = () => _json<InfraResp>('/infra');
export const getGraph      = (window = '5m') => _json<TopoGraph>(`/graph?window=${encodeURIComponent(window)}`);
export const getTraffic    = (window = '5m') => _json<TrafficResp>(`/traffic?window=${encodeURIComponent(window)}`);
export const getCluster    = () => _json<ClusterResp>('/cluster');
export const getNodeDetail = (id: string) => _json<any>(`/node?id=${encodeURIComponent(id)}`);
export const getNodeLogs   = (id: string, container?: string, tail = 150) =>
  _json<NodeLogs>(`/logs?id=${encodeURIComponent(id)}${container ? `&container=${encodeURIComponent(container)}` : ''}&tail=${tail}`);

// per-node error-log click-through (Loki via the gateway)
export type NodeErrors = {
  id: string; window: string;
  lines: Array<{ ts: number; pod: string; line: string }>;
  note?: string;
};
export const getNodeErrors = (id: string, window = '1h', limit = 25) =>
  _json<NodeErrors>(`/errors?id=${encodeURIComponent(id)}&window=${window}&limit=${limit}`);
