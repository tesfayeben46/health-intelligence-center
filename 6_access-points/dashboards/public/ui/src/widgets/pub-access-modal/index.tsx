/**
 * Widget · pub-access-modal: access-request modal (form + success view).
 *
 * POSTs to /api/proxy/access-requests (FastAPI proxies to the gateway's
 * /api/public/access-requests endpoint, which returns the tracking ID and,
 * on a duplicate pending request, a 409). Reads closeModal + setPage from the
 * host: a public requester is never linked to the admin console.
 *
 * Identity verification is an email-OTP round-trip against Fayda:
 *   1. The visitor enters their Fayda FAN and clicks "Send code". We POST
 *      /api/proxy/fayda/challenge {fan}; the gateway confirms the FAN exists in
 *      Fayda, emails a 6-digit code to the identity's registered address, and
 *      returns the masked email.
 *   2. The visitor enters the code and clicks "Verify". We POST
 *      /api/proxy/fayda/verify {fan, code}; on success the gateway returns the
 *      verified identity and we set verified_via='fayda' + fayda_id.
 * The section is optional (organisation accounts don't require it). Fayda is
 * the only verifier; there is no National ID / Passport path.
 *
 * Explicit states:
 *   not-started to sending to code-sent to verifying to verified
 *                                     ↘ (bad_code / expired stay on code-sent)
 */
import { useState, useMemo } from 'react';
import { useProfile } from '../../hooks/useProfile';
import type { PublicWidget, PublicHost } from '../../runtime/types';

/** Explicit verification lifecycle: one clear status is rendered per state. */
type VerifyState =
  | 'not-started'   // nothing sent yet
  | 'sending'       // challenge in flight (emailing the code)
  | 'code-sent'     // code emailed; awaiting the 6-digit entry
  | 'verifying'     // verify in flight
  | 'verified';     // Fayda confirmed the code

type AccessForm = {
  name: string; email: string; phone: string; org: string; role: string;
  dashboards: string[]; purpose: string;
  fan: string;                        // the entered Fayda FAN
  faydaEmail: string;                 // the email registered to that FAN (the pair)
  code: string;                       // the 6-digit OTP the visitor received
  verifyState: VerifyState;
  maskedEmail: string;                // e.g. "b•••@gmail.com": shown after send
  fayda_id: string;                   // set only on a successful Fayda verify
  verified_via: '' | 'fayda';         // 'fayda' only after a successful verify
};

const EMPTY: AccessForm = {
  name: '', email: '', phone: '', org: '', role: '', dashboards: [], purpose: '',
  fan: '', faydaEmail: '', code: '', verifyState: 'not-started', maskedEmail: '',
  fayda_id: '', verified_via: '',
};

type FieldErr = { name?: string; email?: string; purpose?: string; fan?: string; faydaEmail?: string; code?: string };

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DEFAULT_ID_RE = /^\d{12,16}$/;   // Ethiopia's FIN is 12 digits, the FAN 16.

/** Format-check the national-ID number (shape only, not a verification). The
 *  pattern, label and hint all come from the active profile's `identity` block,
 *  so a country with a different national-ID shape (or none) is not hardcoded. */
function validateId(value: string, re: RegExp, label: string, hint: string): string | null {
  const v = value.trim();
  if (!v) return `Enter your ${label} first.`;
  return re.test(v) ? null : hint;
}

/** Turn a gateway access-request error into a friendly line. */
function friendlyError(status: number, body: any): string {
  const detail = body?.detail;
  if (status === 409) {
    if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
    if (typeof detail === 'string') return detail;
    return 'You already have a pending request for this access. We will review it shortly.';
  }
  if (status === 422) {
    const fields = new Set<string>();
    if (Array.isArray(detail)) {
      for (const d of detail) {
        const loc = Array.isArray(d?.loc) ? d.loc : [];
        const f = loc[loc.length - 1];
        if (f === 'full_name') fields.add('full name');
        else if (f === 'email') fields.add('email');
        else if (f === 'purpose') fields.add('purpose');
        else if (typeof f === 'string') fields.add(String(f));
      }
    }
    return fields.size
      ? `Please check these fields and try again: ${Array.from(fields).join(', ')}.`
      : 'Please review the form and try again.';
  }
  if (status === 502 || status === 503)
    return 'The service is temporarily unavailable. Please try again in a moment.';
  return 'Something went wrong submitting your request. Please try again in a moment.';
}

/** Map a Fayda challenge/verify `reason` to a friendly line. */
function faydaReason(reason: string | undefined): string {
  switch (reason) {
    case 'not_found':          return 'No Fayda record matches that number. Check the digits, or continue without verification.';
    case 'invalid_fan':        return 'Enter a valid Fayda number (12 to 16 digits).';
    case 'rate_limited':       return 'Please wait a moment before requesting another code.';
    case 'no_email_on_record': return 'This Fayda identity has no email on record, so we cannot send a code.';
    case 'email_required':      return 'Enter the email registered to your Fayda number.';
    case 'email_mismatch':      return 'That email does not match this Fayda number. Check both and try again.';
    case 'bad_code':           return 'That code is not correct. Check the digits and try again.';
    case 'expired':            return 'That code has expired: request a new one.';
    case 'too_many_attempts':  return 'Too many incorrect attempts. Request a new code to try again.';
    case 'service_unavailable': return 'The Fayda verification service could not be reached. Please try again in a moment.';
    default:                   return 'We could not complete verification. Please try again in a moment.';
  }
}

function AccessRequestModal({ host }: { host: PublicHost }) {
  const onClose = host.closeModal;
  const { setPage } = host;
  // Access-request copy + identity verification are country facts. The whole
  // verification card renders only when the active profile ships an `identity`
  // block (et-v3: Fayda); a country with no national-ID verifier omits it, so
  // the modal never offers a form that posts to an endpoint absent there.
  const { copy, profile } = useProfile();
  const ac = (copy?.access ?? {}) as Record<string, string>;
  const idcfg = profile.identity;
  const idLabel = idcfg?.id_label ?? 'ID number';
  const idHint = idcfg?.id_hint ?? 'Enter a valid number.';
  const idRe = useMemo(() => {
    if (!idcfg?.id_pattern) return DEFAULT_ID_RE;
    try { return new RegExp(idcfg.id_pattern); } catch { return DEFAULT_ID_RE; }
  }, [idcfg?.id_pattern]);
  const reviewOrg = ac.review_org || 'the platform administrators';
  const [form, setForm] = useState<AccessForm>(EMPTY);
  const [fieldErr, setFieldErr] = useState<FieldErr>({});
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>('');
  // Inline status under the identity card: a friendly line + tone.
  const [verifyMsg, setVerifyMsg] =
    useState<{ tone: 'good' | 'watch' | 'alert'; text: string } | null>(null);

  const update = (patch: Partial<AccessForm>) => setForm(f => ({ ...f, ...patch }));

  // Editing the FAN invalidates any code already sent + any prior verification.
  const changeFan = (value: string) => {
    setFieldErr(fe => ({ ...fe, fan: undefined }));
    setVerifyMsg(null);
    setForm(f => ({
      ...f, fan: value, code: '', maskedEmail: '',
      verifyState: 'not-started', fayda_id: '', verified_via: '',
    }));
  };

  // Editing the email also invalidates any code already sent + prior verification.
  const changeFaydaEmail = (value: string) => {
    setFieldErr(fe => ({ ...fe, faydaEmail: undefined }));
    setVerifyMsg(null);
    setForm(f => ({
      ...f, faydaEmail: value, code: '', maskedEmail: '',
      verifyState: 'not-started', fayda_id: '', verified_via: '',
    }));
  };

  // Step 1: confirm the FAN + registered email pair, then email a code to it.
  const sendCode = async () => {
    const e = validateId(form.fan, idRe, idLabel, idHint);
    if (e) { setFieldErr(fe => ({ ...fe, fan: e })); return; }
    const em = form.faydaEmail.trim();
    if (!EMAIL_RE.test(em)) {
      setFieldErr(fe => ({ ...fe, faydaEmail: 'Enter the email registered to your Fayda number.' }));
      return;
    }
    setFieldErr(fe => ({ ...fe, fan: undefined, faydaEmail: undefined }));
    setVerifyMsg(null);
    setErr('');
    update({ verifyState: 'sending' });
    const fan = form.fan.trim();
    try {
      const r = await fetch('/api/proxy/fayda/challenge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fan, email: em }),
      });
      const body = await r.json().catch(() => ({}));
      if (r.ok && body.ok) {
        setForm(f => ({ ...f, verifyState: 'code-sent', code: '',
                        maskedEmail: body.masked_email || '' }));
        setVerifyMsg({ tone: 'good',
          text: `We sent a 6-digit code to ${body.masked_email || 'your registered email'}. Enter it below to finish.` });
      } else {
        setForm(f => ({ ...f, verifyState: 'not-started' }));
        setVerifyMsg({ tone: 'alert', text: faydaReason(body.reason) });
      }
    } catch {
      setForm(f => ({ ...f, verifyState: 'not-started' }));
      setVerifyMsg({ tone: 'alert',
        text: 'We could not reach the verification service. Please try again in a moment.' });
    }
  };

  // Step 2: submit the code the visitor received by email.
  const verifyCode = async () => {
    const code = form.code.trim();
    if (!/^\d{4,8}$/.test(code)) {
      setFieldErr(fe => ({ ...fe, code: 'Enter the 6-digit code from the email.' }));
      return;
    }
    setFieldErr(fe => ({ ...fe, code: undefined }));
    setErr('');
    update({ verifyState: 'verifying' });
    const fan = form.fan.trim();
    try {
      const r = await fetch('/api/proxy/fayda/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fan, code }),
      });
      const body = await r.json().catch(() => ({}));
      if (r.ok && body.ok) {
        setForm(f => ({
          ...f,
          verifyState: 'verified',
          verified_via: 'fayda',
          fayda_id: body.fayda_id || fan,
          name:  f.name  || body.name || `${body.given_name || ''} ${body.family_name || ''}`.trim(),
          phone: f.phone || body.phone_number || '',
        }));
        setVerifyMsg({ tone: 'good',
          text: `Identity verified with Fayda${body.fayda_id ? ` (${body.fayda_id})` : ''}. Reviewers will see a verified badge.` });
      } else {
        // Stay on 'code-sent' so the visitor can retry the code or resend.
        setForm(f => ({ ...f, verifyState: 'code-sent' }));
        setVerifyMsg({ tone: 'alert', text: faydaReason(body.reason) });
      }
    } catch {
      setForm(f => ({ ...f, verifyState: 'code-sent' }));
      setVerifyMsg({ tone: 'alert',
        text: 'We could not reach the verification service. Please try again in a moment.' });
    }
  };

  const toggleDash = (id: string, on: boolean) =>
    update({ dashboards: on
      ? Array.from(new Set([...form.dashboards, id]))
      : form.dashboards.filter(d => d !== id) });

  const onBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };

  // Mirror the backend PublicSubmission constraints so a short purpose / bad
  // email is caught inline before we submit (no round-trip 422).
  const validate = (): boolean => {
    const fe: FieldErr = {};
    if (form.name.trim().length < 2) fe.name = 'Enter your full name (at least 2 characters).';
    if (!EMAIL_RE.test(form.email.trim())) fe.email = 'Enter a valid email address.';
    if (form.purpose.trim().length < 10) fe.purpose = 'Describe your intended use in at least 10 characters.';
    setFieldErr(fe);
    return Object.keys(fe).length === 0;
  };

  const submit = async () => {
    setErr('');
    if (!validate()) return;
    setBusy(true);
    try {
      // Only send filled optionals: empty strings become omitted so nothing
      // invalid reaches the backend.
      const payload: Record<string, unknown> = {
        full_name: form.name.trim(),
        email:     form.email.trim(),
        purpose:   form.purpose.trim(),
      };
      if (form.phone.trim()) payload.phone = form.phone.trim();
      if (form.org.trim())   payload.org   = form.org.trim();
      if (form.role.trim())  payload.role  = form.role.trim();
      if (form.dashboards.length) payload.dashboards = form.dashboards;
      // Identity: id_type/fayda_id/verified_via='fayda' only after a successful verify.
      if (form.verifyState === 'verified' && form.verified_via === 'fayda') {
        payload.id_type = 'fayda';
        payload.fayda_id = form.fayda_id || form.fan.trim();
        payload.verified_via = 'fayda';
      }
      const r = await fetch('/api/proxy/access-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(friendlyError(r.status, body)); return; }
      setSubmitted(body.tracking_id);
      // Reset the form after a successful submit so a re-open starts clean.
      setForm(EMPTY);
      setFieldErr({});
      setVerifyMsg(null);
    } catch {
      setErr('We could not reach the server. Please check your connection and try again.');
    } finally {
      setBusy(false);
    }
  };

  // Success-view actions: user-authorized only, never a link to the admin console.
  const closeAll = () => { setSubmitted(null); setForm(EMPTY); setFieldErr({}); setErr(''); setVerifyMsg(null); onClose(); };
  const returnToDatasets = () => { setSubmitted(null); setForm(EMPTY); setFieldErr({}); setErr(''); setVerifyMsg(null); setPage('data'); onClose(); };

  const verified = form.verifyState === 'verified';
  const codeSent = form.verifyState === 'code-sent' || form.verifyState === 'verifying';
  const sending  = form.verifyState === 'sending';
  const toneColor = (t: 'good' | 'watch' | 'alert') =>
    t === 'good' ? 'var(--good, #1d8b5a)'
    : t === 'alert' ? 'var(--alert, #c0392b)'
    : 'var(--watch, #b7791f)';

  return (
    <div className="modal-bg" onClick={onBackdrop}>
      <div className="modal" style={{ maxWidth: 640 }}>

        {!submitted && (
          <>
            <div className="modal-hdr">
              <h2>{ac.title || 'Request access'}</h2>
              <p>{ac.subtitle || 'Tell us who you are and what you need. We respond within 2 business days.'}</p>
            </div>
            <div className="modal-body">
              {/* Identity verification: rendered only when the active profile
                  ships an `identity` block (Fayda for et-v3); omitted entirely
                  for a country with no national-ID verifier. */}
              {idcfg && (
              <div style={{
                background: verified ? 'var(--good-50, #e7f5ee)' : 'var(--paper-2)',
                border: verified ? '1px solid var(--good, #1d8b5a)' : '1px solid var(--border)',
                borderRadius: 'var(--radius-lg)', padding: '0.9rem 1.1rem',
                marginBottom: '1rem',
              }} data-verify-state={form.verifyState}>
                <div style={{ display: 'flex', gap: '0.85rem', alignItems: 'flex-start' }}>
                  <span className="ico-circle tint-navy" style={{ flexShrink: 0 }}>
                    <svg><use href="#i-shield-check" /></svg>
                  </span>
                  <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                    <p className="text-sm" style={{ margin: 0, fontWeight: 600 }}>
                      {idcfg.card_title || `Verify your identity with ${idcfg.system} (optional)`}
                    </p>
                    <p className="text-xs muted" style={{ margin: '0.2rem 0 0' }}>
                      {idcfg.help_text || `Enter your ${idLabel} and the email registered to it; `
                        + 'we send a one-time code to that address to confirm it is you. Helps '
                        + 'reviewers approve faster. Not required for organisation accounts.'}
                    </p>
                  </div>
                </div>

                {/* Step 1: ID number + registered email (the pair) + Send code */}
                <div className="field-group" style={{ marginTop: '0.8rem' }}>
                  <label>{idLabel}</label>
                  <input type="text" inputMode="numeric"
                         value={form.fan} placeholder={idcfg.id_placeholder || ''}
                         aria-invalid={!!fieldErr.fan}
                         disabled={verified}
                         onChange={e => changeFan(e.target.value)} />
                  {fieldErr.fan && (
                    <p className="text-xs" style={{ margin: '0.35rem 0 0', color: 'var(--alert, #c0392b)' }}>
                      {fieldErr.fan}
                    </p>
                  )}
                </div>
                <div className="field-group" style={{ marginTop: '0.6rem' }}>
                  <label>{idcfg.email_label || 'Email on your ID record'}</label>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <input type="email" inputMode="email"
                           value={form.faydaEmail} placeholder="you@example.com"
                           aria-invalid={!!fieldErr.faydaEmail}
                           disabled={verified}
                           onChange={e => changeFaydaEmail(e.target.value)}
                           style={{ flex: '1 1 auto', minWidth: 0 }} />
                    <button type="button" className="btn btn-sm btn-primary"
                            onClick={sendCode}
                            disabled={sending || verified || form.verifyState === 'verifying'}
                            style={{ flexShrink: 0 }}>
                      {sending ? 'Sending' : verified ? 'Verified' : codeSent ? 'Resend code' : 'Send code'}
                    </button>
                  </div>
                  {fieldErr.faydaEmail && (
                    <p className="text-xs" style={{ margin: '0.35rem 0 0', color: 'var(--alert, #c0392b)' }}>
                      {fieldErr.faydaEmail}
                    </p>
                  )}
                </div>

                {/* Step 2: code entry (revealed after a code is sent) */}
                {codeSent && !verified && (
                  <div className="field-group" style={{ marginTop: '0.7rem' }}>
                    <label>6-digit code</label>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <input type="text" inputMode="numeric" maxLength={8}
                             value={form.code} placeholder="123456"
                             aria-invalid={!!fieldErr.code}
                             onChange={e => { setFieldErr(fe => ({ ...fe, code: undefined }));
                                              update({ code: e.target.value }); }}
                             style={{ flex: '1 1 auto', minWidth: 0, letterSpacing: '0.25em' }} />
                      <button type="button" className="btn btn-sm btn-primary"
                              onClick={verifyCode}
                              disabled={form.verifyState === 'verifying'}
                              style={{ flexShrink: 0 }}>
                        {form.verifyState === 'verifying' ? 'Verifying' : 'Verify'}
                      </button>
                    </div>
                    {fieldErr.code && (
                      <p className="text-xs" style={{ margin: '0.35rem 0 0', color: 'var(--alert, #c0392b)' }}>
                        {fieldErr.code}
                      </p>
                    )}
                  </div>
                )}

                {verifyMsg && (
                  <p className="text-xs" role="status" aria-live="polite"
                     style={{ margin: '0.6rem 0 0', fontWeight: 500, color: toneColor(verifyMsg.tone) }}>
                    {verifyMsg.text}
                  </p>
                )}
              </div>
              )}

              <div className="grid grid-2 gap-sm">
                <div className="field-group"><label>Full name</label>
                  <input type="text" value={form.name} placeholder={ac.ph_name || 'Full name'}
                         aria-invalid={!!fieldErr.name}
                         onChange={e => { update({ name: e.target.value }); if (fieldErr.name) setFieldErr(fe => ({ ...fe, name: undefined })); }} />
                  {fieldErr.name && <p className="text-xs" style={{ margin: '0.25rem 0 0', color: 'var(--alert, #c0392b)' }}>{fieldErr.name}</p>}
                </div>
                <div className="field-group"><label>Email</label>
                  <input type="email" value={form.email} placeholder={ac.ph_email || 'name@example.org'}
                         aria-invalid={!!fieldErr.email}
                         onChange={e => { update({ email: e.target.value }); if (fieldErr.email) setFieldErr(fe => ({ ...fe, email: undefined })); }} />
                  {fieldErr.email && <p className="text-xs" style={{ margin: '0.25rem 0 0', color: 'var(--alert, #c0392b)' }}>{fieldErr.email}</p>}
                </div>
                <div className="field-group"><label>Phone</label>
                  <input type="tel" value={form.phone} placeholder={ac.ph_phone || 'Phone number'}
                         onChange={e => update({ phone: e.target.value })} /></div>
                <div className="field-group"><label>Organisation</label>
                  <input type="text" value={form.org} placeholder={ac.ph_org || 'Organisation'}
                         onChange={e => update({ org: e.target.value })} /></div>
              </div>
              <div className="field-group">
                <label>Role / job title</label>
                <input type="text" value={form.role}
                       placeholder={ac.ph_role || 'Programme officer, director, researcher'}
                       onChange={e => update({ role: e.target.value })} />
              </div>
              <div className="field-group">
                <label>Dashboards requested</label>
                <div className="check-list">
                  {[
                    { v: 'internal-bi',        label: 'Internal Business Intelligence',           extra: null },
                    { v: 'infra-admin',        label: 'Infrastructure & Access Management',       extra: <span className="pill alert text-xs">privileged</span> },
                    { v: 'community-workshop', label: 'Community Workshop Portal',                extra: null },
                    { v: 'submission-portal',  label: 'Data Submission Portal',                   extra: null },
                    { v: 'research-api',       label: 'Research-tier API access',                 extra: null },
                  ].map(d => (
                    <label key={d.v}>
                      <input type="checkbox" value={d.v}
                             checked={form.dashboards.includes(d.v)}
                             onChange={e => toggleDash(d.v, e.target.checked)} />
                      <span>{d.label}</span>{d.extra}
                    </label>
                  ))}
                </div>
              </div>
              <div className="field-group">
                <label>Purpose &amp; intended use</label>
                <textarea rows={3} value={form.purpose}
                          aria-invalid={!!fieldErr.purpose}
                          placeholder="Briefly describe how you intend to use the data, your project, and the time horizon."
                          onChange={e => { update({ purpose: e.target.value }); if (fieldErr.purpose) setFieldErr(fe => ({ ...fe, purpose: undefined })); }} />
                {fieldErr.purpose && <p className="text-xs" style={{ margin: '0.25rem 0 0', color: 'var(--alert, #c0392b)' }}>{fieldErr.purpose}</p>}
              </div>
              <div className="field-group">
                <label>Supporting documentation</label>
                <input type="file" multiple />
                <p className="text-xs muted">Supervisor letter, IRB approval, project proposal, employment verification (PDF/DOCX, up to 10MB each)</p>
              </div>
              <p className="text-xs muted">
                By submitting, you accept the <a href="#">data use agreement</a> and confirm you will not redistribute restricted data.
              </p>
              {err && <p className="text-sm" role="alert" style={{ color: 'var(--alert)' }}>{err}</p>}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={submit} disabled={busy}>
                {busy ? 'Submitting' : 'Submit request'}
              </button>
            </div>
          </>
        )}

        {submitted && (
          <>
            <div className="modal-hdr">
              <h2><svg className="ico"><use href="#i-check-circle" /></svg> Request submitted</h2>
              <p>{ac.success_sub || `Your request has been queued for ${reviewOrg} review.`}</p>
            </div>
            <div className="modal-body">
              <div style={{ textAlign: 'center', padding: '1.2rem 0' }}>
                <span className="ico-circle tint-navy"
                      style={{ width: 64, height: 64, marginBottom: '0.6rem' }}>
                  <svg style={{ width: 32, height: 32 }}><use href="#i-mail" /></svg>
                </span>
                <p className="text-sm muted">Your tracking ID</p>
                <p style={{
                  fontFamily: 'var(--font-mono)', fontSize: '1.6rem', fontWeight: 700,
                  color: 'var(--navy-800)', letterSpacing: '0.04em',
                  margin: '0.2rem 0 0.6rem',
                }}>
                  <span>{submitted}</span>
                </p>
                <p className="text-sm muted">{ac.success_note || `Keep this ID. You will receive an email confirmation, and ${reviewOrg} will respond within 2 business days.`}</p>
              </div>

              <div style={{ background: 'var(--paper-2)', borderRadius: 'var(--radius-lg)', padding: '0.95rem 1.1rem', marginTop: '0.6rem' }}>
                <p className="text-sm" style={{ marginBottom: '0.4rem' }}><strong>What happens next</strong></p>
                <ol className="text-sm" style={{ paddingLeft: '1.2rem', color: 'var(--ink-2)', margin: 0 }}>
                  <li>A system administrator reviews your scope and supporting documents</li>
                  <li>You receive an approval or denial email with sign-in instructions</li>
                  <li>Once approved, open the dashboard you were granted from the <strong>Dashboards</strong> menu and sign in there</li>
                </ol>
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={closeAll}>Close</button>
              <button className="btn btn-primary" onClick={returnToDatasets}>Return to datasets</button>
            </div>
          </>
        )}

      </div>
    </div>
  );
}

const widget: PublicWidget = { id: 'pub-access-modal', Component: AccessRequestModal };
export default widget;
