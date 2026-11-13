export type Scenario = {
  id: string;
  title: string;
  page?: string;
  categories?: string[];
  category?: string;
  affected_dashboards?: string[];
  prerequisites?: string[];
  credentials_needed?: string[];
  steps?: string[];
  expected_result?: string;
  demo_value?: number;
  why_it_matters?: string;
  trigger?: { kind: string; args?: Record<string, unknown> };
};

export type Credential = {
  username: string;
  password: string;
  realm: string;
  role_label: string;
  full_name?: string;
  description?: string;
  sign_in_at?: string[];
};

export type DemoPage = {
  id: string;
  title: string;
  description: string;
  icon?: string;
  scenario_count: number;
};

export async function fetchPages(): Promise<{ pages: DemoPage[] }> {
  const r = await fetch('/api/demo/pages');
  if (!r.ok) throw new Error(`fetch pages: ${r.status}`);
  return r.json();
}

export async function fetchScenarios(opts: { page?: string; category?: string } = {})
    : Promise<{ count: number; scenarios: Scenario[]; categories: string[] }> {
  const qp = new URLSearchParams();
  if (opts.page)     qp.set('page', opts.page);
  if (opts.category) qp.set('category', opts.category);
  const url = '/api/demo/scenarios' + (qp.toString() ? `?${qp}` : '');
  const r = await fetch(url);
  if (!r.ok) throw new Error(`fetch scenarios: ${r.status}`);
  return r.json();
}

export async function fetchScenario(id: string): Promise<Scenario> {
  const r = await fetch(`/api/demo/scenarios/${id}`);
  if (!r.ok) throw new Error(`fetch scenario ${id}: ${r.status}`);
  return r.json();
}

export async function fetchCredentials(): Promise<{ count: number; credentials: Credential[] }> {
  const r = await fetch('/api/demo/credentials');
  if (!r.ok) throw new Error(`fetch credentials: ${r.status}`);
  return r.json();
}

export async function triggerScenario(id: string, overrideArgs?: Record<string, unknown>) {
  const r = await fetch(`/api/demo/scenarios/${id}/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(overrideArgs || {}),
  });
  const body = await r.json().catch(() => ({ raw: 'unparseable response' }));
  return { status: r.status, body };
}

export async function reloadCatalog() {
  const r = await fetch('/api/demo/reload', { method: 'POST' });
  return r.json();
}
