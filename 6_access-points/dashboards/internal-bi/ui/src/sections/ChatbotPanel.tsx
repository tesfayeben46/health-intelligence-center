/**
 * Internal-BI assistant: floating button + chat panel.
 *
 * Drives the gateway's progressive tool-call protocol:
 *
 *   send() to POST /api/chat/start to _step
 *   while _step.kind === 'tool_call' && _step.executor === 'spa':
 *      result = window.__nhdwDashboard[tool.name](tool.args)
 *      _step  = POST /api/chat/continue { session_id, tool_result }
 *   render _step.answer + any collapsible tool_calls
 *
 * Gateway-executor tool calls (run_sql) never reach the SPA: the gateway
 * handles them internally within a single /api/chat/start request and
 * returns the final answer. Their results show up in `tool_calls[]` so the
 * user can inspect SQL + rows.
 *
 * Conversation history is session-only; we keep the last few rounds in
 * `history` and resend them with each new question so the bot has
 * context. No persistence across reloads.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { authFetch } from '../api/useAuth';
import type { DashboardApi } from '../api/dashboardApi';
import { useBiApp } from '../useBiApp';

type ToolCall  = { kind: string; args: any; result: any };
type Step      =
  | { kind: 'tool_call'; tool: { name: string; args: any }; executor: 'spa' | 'gateway' }
  | { kind: 'answer';    answer: string; tool_calls: ToolCall[] };
type StartResp = Step & { session_id: string };

type ChatTurn =
  | { who: 'user';  text: string }
  | { who: 'bot';   text: string; tool_calls?: ToolCall[] }
  | { who: 'tool';  label: string };           // transient placeholder during a SPA tool round

type Intro = { suggestions: string[]; model: string };

const PAGE_ID = (section: string) => `internal-bi/${section}`;
const POST_JSON = { 'Content-Type': 'application/json' };

export default function ChatbotPanel() {
  const { state } = useBiApp();
  const [open, setOpen]           = useState(false);
  const [busy, setBusy]           = useState(false);
  const [draft, setDraft]         = useState('');
  const [turns, setTurns]         = useState<ChatTurn[]>([]);
  const [intro, setIntro]         = useState<Intro | null>(null);
  const [error, setError]         = useState<string | null>(null);
  const [unavailable, setUnavail] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  // Load intro on mount so we can hide the FAB for users without the
  // bi-assistant grant. Retry once if it fires before the SPA's PKCE
  // exchange has stored the access token.
  useEffect(() => {
    let cancelled = false;
    let tries = 0;
    async function load() {
      while (!cancelled && tries < 6) {
        tries++;
        if (!window.__nhdwAccessToken) {
          await new Promise(res => setTimeout(res, 200));
          continue;
        }
        try {
          const r = await authFetch(`/gw/chat/intro?dashboard_id=internal-bi`);
          if (r.status === 403) { if (!cancelled) setUnavail(true); return; }
          if (!r.ok) throw new Error(`intro ${r.status}`);
          const j = await r.json();
          if (!cancelled) setIntro(j);
          return;
        } catch (e: any) {
          await new Promise(res => setTimeout(res, 300));
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  // Scroll on new turn.
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [turns, busy]);

  const send = useCallback(async (text?: string) => {
    const q = (text ?? draft).trim();
    if (!q || busy) return;
    setError(null);
    setDraft('');
    setTurns(t => [...t, { who: 'user', text: q }]);
    setBusy(true);

    // Build history window: last 4 turns of (user, bot) pairs. Tool placeholders
    // don't get re-sent: the gateway's session has the real tool messages.
    const history = turns
      .filter(t => t.who === 'user' || t.who === 'bot')
      .slice(-8)
      .map(t => ({ role: t.who === 'user' ? 'user' : 'assistant',
                   content: (t as any).text }));

    try {
      const startResp = await fetchJson('/gw/chat/start', {
        method: 'POST', headers: POST_JSON,
        body: JSON.stringify({
          dashboard_id: 'internal-bi',
          page_id:      PAGE_ID(state.active),
          question:     q,
          history,
        }),
      }) as StartResp;
      await driveStep(startResp.session_id, startResp);
    } catch (e: any) {
      setTurns(t => [...t, { who: 'bot', text: `Error: ${e?.message || e}` }]);
    } finally {
      setBusy(false);
    }
  }, [draft, busy, turns, state.active]);

  const driveStep = useCallback(async (sessionId: string, step: Step): Promise<void> => {
    if (step.kind === 'answer') {
      setTurns(t => [...t, { who: 'bot', text: step.answer, tool_calls: step.tool_calls }]);
      return;
    }
    // SPA-executor tool call.
    const api = window.__nhdwDashboard as DashboardApi | undefined;
    let tool_result: any;
    if (!api || !(api as any)[step.tool.name]) {
      tool_result = { ok: false, error: `SPA tool ${step.tool.name} unavailable` };
    } else {
      try {
        const fn = (api as any)[step.tool.name];
        const out = fn(step.tool.args || {});
        tool_result = out instanceof Promise ? await out : out;
      } catch (e: any) {
        tool_result = { ok: false, error: e?.message || String(e) };
      }
    }
    setTurns(t => [...t, { who: 'tool', label: `Used ${step.tool.name}` }]);
    const next = await fetchJson('/gw/chat/continue', {
      method: 'POST', headers: POST_JSON,
      body: JSON.stringify({ session_id: sessionId, tool_result }),
    }) as Step;
    await driveStep(sessionId, next);
  }, []);

  if (unavailable) {
    // R-OPS-ADM, R-CONS-RES, etc. don't have the bi-assistant widget
    // grant on internal-bi. We just don't render the FAB.
    return null;
  }

  return (
    <>
      <button
        className="ai-fab"
        aria-label="Open AI assistant"
        onClick={() => setOpen(o => !o)}
        title="AI assistant">
        <span className="ai-fab-pulse" aria-hidden />
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2a10 10 0 0 0-10 10c0 1.96.56 3.79 1.54 5.34L2 22l4.66-1.54A10 10 0 1 0 12 2Z" />
          <circle cx="9" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="15" cy="12" r="1" />
        </svg>
      </button>

      {open && (
        <div className="ai-panel" role="dialog" aria-label="AI assistant">
          <header className="ai-panel-head">
            <div className="ai-h-ico" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                   strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12c0 4.97-4.03 9-9 9s-9-4.03-9-9 4.03-9 9-9 9 4.03 9 9Z" />
                <path d="M8 12h.01M12 12h.01M16 12h.01" />
              </svg>
            </div>
            <div>
              <div className="ai-h-title">Palladium Assistant</div>
              <div className="ai-h-sub">
                {intro?.model || 'local LLM'} · this dashboard
              </div>
            </div>
            <button className="ai-h-close" onClick={() => setOpen(false)} aria-label="Close">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 6 6 18M6 6l12 12" />
              </svg>
            </button>
          </header>

          <div className="ai-panel-body" ref={bodyRef}>
            {turns.length === 0 && (
              <>
                <div className="ai-msg bot">
                  Hi, I can read what's on the dashboard, run SQL within your
                  permitted scope, and interact with widgets on your behalf.
                  What would you like to look at?
                </div>
                {intro?.suggestions?.length ? (
                  <div className="ai-suggestions">
                    {intro.suggestions.map(s => (
                      <button key={s} className="ai-suggestion"
                              onClick={() => send(s)} disabled={busy}>
                        {s}
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            )}

            {turns.map((t, i) => (
              <Turn key={i} t={t} />
            ))}

            {busy && (
              <div className="ai-msg bot" aria-live="polite">
                <span className="ai-spinner">…</span>
              </div>
            )}
            {error && (
              <div className="ai-msg bot" style={{ background: '#fde7e7', color: '#8a1a1a' }}>
                {error}
              </div>
            )}
          </div>

          <form className="ai-panel-foot"
                onSubmit={e => { e.preventDefault(); send(); }}>
            <input
              type="text"
              placeholder={busy ? 'Working…' : 'Ask about this dashboard…'}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              disabled={busy}
              autoFocus
            />
            <button type="submit" disabled={busy || !draft.trim()} aria-label="Send">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m22 2-7 20-4-9-9-4Z" />
                <path d="M22 2 11 13" />
              </svg>
            </button>
          </form>
          <div className="ai-disclaimer">
            Bound to your role's permissions · session-only · no PII logged
          </div>
        </div>
      )}
    </>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Minimal, XSS-safe markdown for assistant messages: escape first, THEN apply
// bold / italic / inline-code / fenced-code / links / lists / line-breaks.
// No external lib (offline cluster: no CDN). Fixes literal ** and ` showing up.
function mdToHtml(src: string): string {
  const blocks: string[] = [];
  let s = escapeHtml(src).replace(/```([\s\S]*?)```/g, (_m, code) => {
    blocks.push('<pre class="ai-code">' + String(code).replace(/^\n/, '') + '</pre>');
    return '@@CB' + (blocks.length - 1) + '@@';
  });
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
       .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
       .replace(/__([^_]+)__/g, '<strong>$1</strong>')
       .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
       .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out: string[] = [];
  let ul = false, ol = false;
  const close = () => { if (ul) { out.push('</ul>'); ul = false; } if (ol) { out.push('</ol>'); ol = false; } };
  for (const ln of s.split('\n')) {
    const mu = ln.match(/^\s*[-*]\s+(.*)$/), mo = ln.match(/^\s*\d+\.\s+(.*)$/);
    if (mu) { if (!ul) { close(); out.push('<ul>'); ul = true; } out.push('<li>' + mu[1] + '</li>'); }
    else if (mo) { if (!ol) { close(); out.push('<ol>'); ol = true; } out.push('<li>' + mo[1] + '</li>'); }
    else if (ln.trim() === '') { close(); }
    else { close(); out.push('<div>' + ln + '</div>'); }
  }
  close();
  return out.join('').replace(/@@CB(\d+)@@/g, (_m, i) => blocks[Number(i)]);
}

function Turn({ t }: { t: ChatTurn }) {
  if (t.who === 'user') {
    return <div className="ai-msg user">{t.text}</div>;
  }
  if (t.who === 'tool') {
    return <div className="ai-tool-pill">⚙ {t.label}</div>;
  }
  return (
    <>
      <div className="ai-msg bot" dangerouslySetInnerHTML={{ __html: mdToHtml(t.text) }} />
      {(t.tool_calls || []).length > 0 && (
        <details className="ai-tool-calls">
          <summary>{t.tool_calls!.length} tool call{t.tool_calls!.length > 1 ? 's' : ''}</summary>
          {t.tool_calls!.map((tc, i) => <ToolCallRow key={i} tc={tc} />)}
        </details>
      )}
    </>
  );
}

function ToolCallRow({ tc }: { tc: ToolCall }) {
  const isSql = tc.kind === 'run_sql';
  return (
    <div className="ai-tool-row">
      <div className="ai-tool-head">
        <code>{tc.kind}</code>
        {tc.result?.ok === false && <span className="ai-tool-bad">error</span>}
      </div>
      {isSql && tc.args?.sql && (
        <pre className="ai-tool-sql">{tc.args.sql}</pre>
      )}
      {tc.result?.ok && tc.result?.result?.rows && (
        <details>
          <summary>{tc.result.result.rows.length} rows</summary>
          <pre className="ai-tool-data">{JSON.stringify(tc.result.result.rows.slice(0, 10), null, 2)}</pre>
        </details>
      )}
      {tc.result?.ok === false && (
        <div className="ai-tool-err">{tc.result.error || 'failed'}</div>
      )}
    </div>
  );
}

async function fetchJson(url: string, init?: RequestInit): Promise<any> {
  const r = await authFetch(url, init);
  const text = await r.text();
  let body: any;
  try { body = JSON.parse(text); } catch { body = { raw: text }; }
  if (!r.ok) {
    const detail = body?.detail || body?.raw || text;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body;
}
