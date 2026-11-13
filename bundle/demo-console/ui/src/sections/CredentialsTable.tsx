import type { Credential } from '../api/demo';

export default function CredentialsTable({ credentials }: { credentials: Credential[] }) {
  return (
    <>
      <div className="card">
        <h2>Demo credentials</h2>
        <p className="muted text-sm">
          Every demo user with their password, role, realm, and where to sign in.
          Passwords are shown because these accounts are sandbox only.
        </p>
      </div>

      {credentials.map((c) => (
        <div key={c.username} className="card">
          <div className="row" style={{ marginBottom: '0.4em' }}>
            <strong style={{ fontSize: '1.05em' }}>{c.full_name || c.username}</strong>
            <span className="tag accent">{c.role_label}</span>
            <span className="muted text-sm">realm: {c.realm}</span>
          </div>
          <div className="row" style={{ marginBottom: '0.6em' }}>
            <span className="muted text-sm">username</span>
            <span className="mono">{c.username}</span>
            <button className="copy-btn"
                    onClick={() => navigator.clipboard.writeText(c.username)}>copy</button>
            {c.password && (
              <>
                <span className="muted text-sm" style={{ marginLeft: '1em' }}>password</span>
                <span className="mono">{c.password}</span>
                <button className="copy-btn"
                        onClick={() => navigator.clipboard.writeText(c.password)}>copy</button>
              </>
            )}
          </div>
          {c.description && <p className="muted text-sm" style={{ marginBottom: '0.5em' }}>{c.description}</p>}
          {c.sign_in_at && c.sign_in_at.length > 0 && (
            <>
              <div className="muted text-xs" style={{ marginBottom: '0.3em' }}>Sign in at</div>
              <ul style={{ margin: 0, paddingLeft: '1.4em' }}>
                {c.sign_in_at.map((url, i) => <li key={i} className="mono text-sm">{url}</li>)}
              </ul>
            </>
          )}
        </div>
      ))}
    </>
  );
}
