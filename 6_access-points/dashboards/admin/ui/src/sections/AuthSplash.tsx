/**
 * Auth splash: full-screen overlay shown while the session is being confirmed
 * (and during the redirect to Keycloak for unauthenticated visitors), so the
 * protected dashboard never flashes before auth resolves. Self-contained inline
 * styles + a scoped keyframe; no external asset or CSS-class dependency.
 */
export default function AuthSplash() {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 99999,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: '1.1rem', background: 'linear-gradient(180deg,#0a1734 0%,#050b20 100%)',
      color: 'rgba(255,255,255,0.82)', fontFamily: "'Nunito Sans', system-ui, sans-serif",
    }}>
      <div style={{
        fontFamily: "'Cormorant Garamond','Georgia',serif", fontSize: '1.7rem',
        fontWeight: 600, letterSpacing: '0.08em', color: '#fff',
      }}>NHDW</div>
      <span style={{
        width: 24, height: 24, borderRadius: '50%', display: 'inline-block',
        border: '3px solid rgba(255,255,255,0.16)', borderTopColor: 'rgba(255,255,255,0.72)',
        animation: 'nhdwspin 0.8s linear infinite',
      }} />
      <p style={{ margin: 0, fontSize: '0.9rem', letterSpacing: '0.02em' }}>Signing you in…</p>
      <style>{'@keyframes nhdwspin{to{transform:rotate(360deg)}}'}</style>
    </div>
  );
}
