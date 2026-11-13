/**
 * useAuth: client-side auth state for the admin SPA.
 *
 * Two paths, chosen at runtime from /auth/config:
 *
 *   DEV_AUTH=true   The backend mocks identity from DEV_MOCK_USER /
 *                   DEV_MOCK_ROLE; the SPA calls /auth/me and gets the mock
 *                   UserCtx. Local development only.
 *
 *   DEV_AUTH=false  Keycloak PKCE. On boot a `?code=` in the URL is exchanged
 *                   for a token, held in memory and sent by every authFetch as
 *                   Authorization: Bearer …. If /auth/me returns
 *                   {authenticated: false}, login() redirects to Keycloak
 *                   /authorize.
 */
import { useEffect, useState, useCallback } from 'react';

type AuthConfig = {
  dev_auth: boolean;
  keycloak_public_base: string;
  realm: string;
  client_id: string;
  redirect_uri: string;
  post_logout_uri: string;
};

type AuthUser = {
  authenticated: boolean;
  kc_sub?: string;
  username?: string;
  email?: string;
  full_name?: string;
  realm?: string;
  primary_role?: string;
  roles?: string[];
  auth_mode?: 'dev' | 'bearer';
};

let _token: string | null = null;

// Persist the bearer across reloads and tab restores in sessionStorage
// (cleared on tab close and on logout), so a reload after the PKCE code was
// consumed keeps the session instead of bouncing back through Keycloak.
const TOKEN_KEY = 'nhdw_admin_at';

// Read-only test surface for Playwright harnesses: the token is also
// exposed on window.__nhdwAccessToken so tests can
// construct Bearer requests without re-running PKCE in every spec.
declare global { interface Window { __nhdwAccessToken?: string | null } }
function setToken(t: string | null) {
  _token = t;
  if (typeof window !== 'undefined') window.__nhdwAccessToken = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch { /* storage unavailable: the in-memory token still works this session */ }
}

// two-stage auth-ready gate
// Data sections (Infra topology, access requests, …) mount and fire /gw/* at
// once, but the PKCE token exchange is async; without a gate those calls race
// ahead of setToken() and 401 on first load. Two gates, chosen by request path:
//   • _tokenReady:   resolves once the token exchange has settled. Gates the
//                    bootstrap's own /auth/me probe so it carries the token.
//   • _sessionReady: resolves only once /auth/me confirms an authenticated
//                    session. Gates every /gw/* proxy call, so gateway calls
//                    never fire unauthenticated. On a signed-out visit the page
//                    redirects to Keycloak and leaves this pending, so the
//                    abandoned page emits no 401s.
// Gates resolve and never reject, so a failed bootstrap cannot hang the
// /auth/me probe.
function makeGate() {
  let resolve!: () => void; let done = false;
  const p = new Promise<void>(r => { resolve = r; });
  return { p, mark: () => { if (!done) { done = true; resolve(); } } };
}
const _tokenGate = makeGate();
const _sessionGate = makeGate();
function markTokenReady() { _tokenGate.mark(); }
function markSessionReady() { _sessionGate.mark(); }

function urlOf(input: RequestInfo): string {
  return typeof input === 'string' ? input : (input as Request).url || '';
}

export async function authFetch(input: RequestInfo, init: RequestInit = {}): Promise<Response> {
  // /gw/* needs an established session; /auth/* only needs the token settled.
  await (/\/gw\//.test(urlOf(input)) ? _sessionGate.p : _tokenGate.p);
  const headers = new Headers(init.headers || {});
  if (_token && !headers.has('authorization')) {
    headers.set('Authorization', `Bearer ${_token}`);
  }
  return fetch(input, { ...init, headers });
}

// Single-flight PKCE code exchange. A fresh mount can run the bootstrap effect
// more than once (dev double-mount, an incidental remount); the authorization
// code is single-use, so a second exchange gets "invalid_grant / Code not valid"
// and could clobber a good session. Dedupe by memoising the in-flight promise so
// the code is redeemed exactly once no matter how many callers race.
let _exchangeInFlight: Promise<string | null> | null = null;
function exchangeOnce(cfg: AuthConfig, code: string, verifier: string): Promise<string | null> {
  if (!_exchangeInFlight) _exchangeInFlight = exchangeCode(cfg, code, verifier);
  return _exchangeInFlight;
}

function base64url(buf: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
}

async function makePkce(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(64)).buffer);
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(hash) };
}

async function exchangeCode(cfg: AuthConfig, code: string, verifier: string): Promise<string | null> {
  const body = new URLSearchParams({
    grant_type:   'authorization_code',
    client_id:    cfg.client_id,
    redirect_uri: cfg.redirect_uri,
    code,
    code_verifier: verifier,
  });
  try {
    const r = await fetch(
      `${cfg.keycloak_public_base}/realms/${cfg.realm}/protocol/openid-connect/token`,
      { method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body });
    if (!r.ok) {
      console.warn('[auth] token exchange failed', r.status, await r.text());
      return null;
    }
    return (await r.json()).access_token ?? null;
  } catch (e) {
    console.warn('[auth] token exchange error', e);
    return null;
  }
}

function stripParam(name: string) {
  const url = new URL(window.location.href);
  if (url.searchParams.has(name)) {
    url.searchParams.delete(name);
    window.history.replaceState({}, '', url.toString());
  }
}

// Failure page, shown only after returning from Keycloak with a ?code that did
// not become a session (a real misconfiguration). Ordinary visits go straight
// to Keycloak. "Try again" reloads; with the code already stripped from the
// URL, the reload starts a clean Keycloak login.
function showAuthError() {
  const publicUrl =
    window.location.protocol + '//' +
    window.location.host.replace(/^[^.]+/, 'public');
  document.documentElement.innerHTML = `
  <head><meta charset="utf-8"><title>Sign-in didn't complete | NHDW</title>
  <style>
    body{font-family:Inter,system-ui,sans-serif;background:#f5efe6;color:#3a2c1e;
         display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
    .card{max-width:480px;background:#fff;border:1px solid #e0d3c0;border-radius:14px;
          padding:40px;box-shadow:0 6px 30px rgba(0,0,0,.08)}
    h1{margin:0 0 10px;font-size:21px;color:#6f4a2e}
    p{line-height:1.6;font-size:15px}
    .btn{display:inline-block;margin:14px 10px 0 0;padding:10px 18px;border-radius:9px;
         background:#9a6a3a;color:#fff;text-decoration:none;border:none;cursor:pointer;font-size:14px}
    .btn.secondary{background:#efe7da;color:#6f4a2e}
  </style></head>
  <body><div class="card">
    <h1>Sign-in didn't complete</h1>
    <p>We returned from the sign-in page without an active session. This is
       usually temporary. Please try again.</p>
    <button class="btn" onclick="location.reload()">Try again</button>
    <a class="btn secondary" href="${publicUrl}">Back to public portal</a>
  </div></body>`;
}

export function useAuth() {
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const c: AuthConfig = await fetch('/auth/config').then(r => r.json());
        if (cancelled) return;
        setCfg(c);

        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        const stateReturned = params.get('state');
        const stored = sessionStorage.getItem('pkce_state');
        const freshReturn = !c.dev_auth && !!code && !!stateReturned && stored === stateReturned;

        if (freshReturn) {
          // Returning from Keycloak: the code is authoritative. Redeem it
          // exactly once (single-flight) and persist the resulting token.
          const verifier = sessionStorage.getItem('pkce_verifier') || '';
          const tok = await exchangeOnce(c, code!, verifier);
          if (tok) setToken(tok);
          sessionStorage.removeItem('pkce_state');
          sessionStorage.removeItem('pkce_verifier');
          // Tidy the callback params so a reload/back doesn't re-trigger the flow.
          ['code', 'state', 'session_state', 'iss'].forEach(stripParam);
        } else if (!c.dev_auth && !_token) {
          // No fresh code: reuse a token persisted from an earlier load so a
          // plain reload does not bounce back through Keycloak. /auth/me below
          // validates it server-side; a stale one is cleared and login restarts.
          const saved = sessionStorage.getItem(TOKEN_KEY);
          if (saved) setToken(saved);
        }

        // Release the token gate: the token (if any) is now set, so the
        // bootstrap's /auth/me probe below carries the Authorization header.
        markTokenReady();

        const me: AuthUser = await authFetch('/auth/me').then(r => r.json());
        if (cancelled) return;

        if (me.authenticated) {
          sessionStorage.removeItem('pkce_fail');   // clear the retry counter
          markSessionReady();                        // release queued /gw/* calls so they return 200 on the first try
          setUser(me);
          return;
        }
        setUser(me);
        // Not authenticated: redirect below and leave the session gate
        // pending, so the /gw/* calls this page queued never fire (no 401 noise).

        // Not signed in: go straight to Keycloak (its page hosts the sign-in
        // options; a live SSO session returns with no prompt). After a ?code
        // that never became a session, retry the login once automatically (the
        // code was likely stale or a duplicate) and show the retry page only if
        // that also fails, so a transient error recovers on its own.
        if (!c.dev_auth) {
          setToken(null);                            // drop any stale persisted token
          if (freshReturn || code) {
            const attempts = Number(sessionStorage.getItem('pkce_fail') || '0');
            if (attempts < 1) { sessionStorage.setItem('pkce_fail', String(attempts + 1)); loginInternal(c); }
            else { sessionStorage.removeItem('pkce_fail'); showAuthError(); }
          } else {
            loginInternal(c);
          }
        }
      } catch (e: any) {
        markTokenReady();                            // never leave the /auth/me probe hanging
        if (!cancelled) setErr(e?.message || String(e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function loginInternal(c: AuthConfig) {
    const { verifier, challenge } = await makePkce();
    const state = base64url(crypto.getRandomValues(new Uint8Array(16)).buffer);
    sessionStorage.setItem('pkce_verifier', verifier);
    sessionStorage.setItem('pkce_state', state);
    // Read ?login_with=<alias> set by the public LoginModal so Keycloak
    // skips its own IdP picker and goes straight to that IdP. Strip from
    // the URL afterwards so back/refresh doesn't repeat the hint.
    const here = new URL(window.location.href);
    const idpHint = here.searchParams.get('login_with') || '';
    if (idpHint) {
      here.searchParams.delete('login_with');
      window.history.replaceState({}, '', here.toString());
    }
    const url = new URL(`${c.keycloak_public_base}/realms/${c.realm}/protocol/openid-connect/auth`);
    url.searchParams.set('response_type',         'code');
    url.searchParams.set('client_id',             c.client_id);
    url.searchParams.set('redirect_uri',          c.redirect_uri);
    url.searchParams.set('scope',                 'openid profile email');  // role claims come from the nhdw-flat-roles DEFAULT scope (auto-applied; requesting unassigned scopes = invalid_scope)
    url.searchParams.set('state',                 state);
    url.searchParams.set('code_challenge',        challenge);
    url.searchParams.set('code_challenge_method', 'S256');
    if (idpHint) url.searchParams.set('kc_idp_hint', idpHint);
    window.location.assign(url.toString());
  }

  const login = useCallback(async () => {
    if (!cfg) return;
    if (cfg.dev_auth) { window.location.reload(); return; }
    await loginInternal(cfg);
  }, [cfg]);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    if (cfg && !cfg.dev_auth) {
      const url = new URL(`${cfg.keycloak_public_base}/realms/${cfg.realm}/protocol/openid-connect/logout`);
      url.searchParams.set('post_logout_redirect_uri', cfg.post_logout_uri);
      url.searchParams.set('client_id', cfg.client_id);
      window.location.assign(url.toString());
    } else {
      window.location.reload();
    }
  }, [cfg]);

  return { cfg, user, err, login, logout };
}
