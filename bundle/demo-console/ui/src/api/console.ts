/**
 * System-console API: available only when the SPA is served by the bundle's
 * host-run demo console (bundle/demo-console/console.py), which manages the
 * whole system's lifecycle. Same-origin, no auth (the console is localhost).
 */
export type ConsoleStatus = {
  cluster_up: boolean;
  pods: { ready: number; total: number };
  sources: Record<string, boolean>;
  runner: { action: string | null; running: boolean; rc: number | null;
            started: number | null; events: number };
  dashboards: Record<string, string>;
};

export type ConsoleEvent = {
  t: number;
  kind: 'start' | 'phase' | 'banner' | 'step' | 'ok' | 'fail' | 'warn'
      | 'line' | 'done' | 'error' | 'cancelled';
  text: string;
};

export async function fetchConsoleStatus(): Promise<ConsoleStatus> {
  const r = await fetch('/api/console/status');
  if (!r.ok) throw new Error(`console status: ${r.status}`);
  return r.json();
}

export async function fireAction(action: 'rebuild' | 'teardown')
    : Promise<{ ok: boolean; message: string }> {
  const r = await fetch('/api/console/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
  return r.json();
}

/** Attach to the live progress stream. Returns a disposer. */
export function streamEvents(
  onEvent: (ev: ConsoleEvent) => void,
  onEnd: () => void,
): () => void {
  const es = new EventSource('/api/console/stream');
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
  es.addEventListener('end', () => { es.close(); onEnd(); });
  es.onerror = () => { /* server restart mid-stream; the poller recovers */ };
  return () => es.close();
}
