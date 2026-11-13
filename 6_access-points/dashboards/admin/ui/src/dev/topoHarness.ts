/**
 * Development-only visual harness for src/topology/renderTopology.ts. It renders
 * a representative synthetic graph (all six zones, live-like edge shapes
 * including traffic ants, an ephemeral lab, a phantom and an external) so the
 * renderer can be inspected or screenshotted without a cluster or auth.
 *
 *   cd 6_access-points/dashboards/admin/ui && bun x vite dev
 *   open http://localhost:5173/topo-harness.html
 *
 * Not part of the production build: vite only builds index.html; this file is
 * reached only through topo-harness.html. Use it as the visual regression
 * check for edge-router and palette changes.
 */
import { renderTopology } from '../topology/renderTopology';
import type { TopoGraph, TopoNode, TopoEdge, TopoTheme } from '../topology/renderTopology';

const theme: TopoTheme = {
  paper: '#ffffff', paper2: '#f6f7f9', bg: '#eef1f5', bg2: '#fafbfc',
  border: '#d7dde6', border2: '#c3cdd9',
  ink: '#0f172a', ink2: '#334155', ink3: '#64748b', ink4: '#94a3b8',
  good: '#16a34a', watch: '#d97706', alert: '#dc2626', info: '#2563eb',
  navy700: '#1e3a5f', navy50: '#eef2f8',
  fontSans: 'system-ui, sans-serif', fontMono: 'ui-monospace, monospace',
};

const N = (id: string, label: string, tier: string, zone: string, extra: Partial<TopoNode> = {}): TopoNode => ({
  id, label, tier, kind: extra.kind ?? 'app', zone,
  declared: true, observed: !extra.phantom, health: extra.phantom ? 'unknown' : 'up',
  phantom: false, k8s: { deployment: id, ready: '1/1', namespace: 'nhdw' },
  ...extra,
});

const E = (source: string, target: string, kind = 'http', traffic: number | null = null): TopoEdge => ({
  id: `${source}->${target}`, source, target, kind,
  declared: true, observed: true, status: 'up',
  traffic: traffic == null ? null : {
    reqRate: traffic, errRate: 0.002, p95Ms: 40, bytes: 120000,
    window: '15m', samples: 500, source: 'gateway_log', series: [1, 2, 3, 2, 4],
  },
});

const graph: TopoGraph = {
  as_of: new Date().toISOString(),
  nodes: [
    N('admin', 'Admin console', 'dashboard', 'access'),
    N('internal-bi', 'Internal BI', 'dashboard', 'access'),
    N('public-dashboard', 'Public dashboard', 'dashboard', 'access'),
    N('workshop', 'Workshop', 'dashboard', 'access'),
    N('dbt-docs', 'dbt Docs', 'dashboard', 'access'),
    N('nhdw-gateway', 'nhdw-gateway', 'api', 'apis', { kind: 'api' }),
    N('postgres-vault', 'Postgres vault', 'storage', 'storage'),
    N('seaweedfs-filer', 'SeaweedFS object store', 'storage', 'storage'),
    N('clickhouse', 'ClickHouse', 'storage', 'storage'),
    N('nhdw-trino', 'Trino', 'engine', 'compute'),
    N('airflow', 'Airflow', 'orchestrator', 'compute'),
    N('mlflow', 'MLflow', 'ml', 'compute'),
    N('nhdw-ollama', 'Ollama LLM', 'ml', 'compute', { kind: 'external' }),
    N('jupyterhub', 'JupyterHub', 'platform', 'compute'),
    N('jupyter-lab-x', 'lab · abebe', 'platform', 'compute', { ephemeral: true }),
    N('keycloak', 'Keycloak', 'security', 'security'),
    N('opa', 'OPA', 'security', 'security'),
    N('fayda-replica', 'Fayda replica', 'external', 'security', { kind: 'external' }),
    N('kubernetes-api', 'Kubernetes API', 'ops', 'observability'),
    N('headlamp', 'Headlamp', 'ops', 'observability'),
    N('prometheus', 'Prometheus', 'observability', 'observability'),
    N('loki', 'Loki', 'observability', 'observability'),
    N('alertmanager', 'Alertmanager', 'observability', 'observability', { phantom: true, health: 'unknown' }),
    N('promtail', 'Promtail', 'observability', 'observability'),
  ],
  edges: [
    E('admin', 'nhdw-gateway', 'http', 3.2),
    E('internal-bi', 'nhdw-gateway', 'http', 8.5),
    E('public-dashboard', 'nhdw-gateway', 'http', 1.1),
    E('workshop', 'nhdw-gateway', 'http', 0.4),
    E('nhdw-gateway', 'nhdw-trino', 'sql', 2.2),
    E('nhdw-gateway', 'postgres-vault', 'sql', 4.0),
    E('nhdw-gateway', 'opa'),
    E('nhdw-gateway', 'keycloak'),
    E('nhdw-gateway', 'kubernetes-api'),        // far target: a route that would otherwise cross the whole box
    E('nhdw-trino', 'clickhouse', 'sql'),
    E('nhdw-trino', 'seaweedfs-filer', 's3'),
    E('nhdw-trino', 'postgres-vault', 'sql'),
    E('airflow', 'seaweedfs-filer', 's3'),
    E('airflow', 'clickhouse', 'sql'),
    E('mlflow', 'seaweedfs-filer', 's3'),
    E('jupyterhub', 'jupyter-lab-x', 'spawns'),
    E('jupyterhub', 'keycloak'),
    E('nhdw-gateway', 'nhdw-ollama'),
    E('keycloak', 'fayda-replica'),
    E('promtail', 'loki'),
    E('prometheus', 'nhdw-gateway'),
    E('prometheus', 'keycloak'),
    E('headlamp', 'kubernetes-api'),
  ],
};

const mount = document.getElementById('topo')!;
renderTopology(mount, graph, theme, {
  onNodeSelect: n => console.log('[harness] node', n.id),
  onEdgeSelect: e => console.log('[harness] edge', e.id),
});
