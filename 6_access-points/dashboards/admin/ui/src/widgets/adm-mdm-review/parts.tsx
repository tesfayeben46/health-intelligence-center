/**
 * Shared pieces of the MDM Stewardship tabs: the load / action error notes
 * and the match-rule vocabulary. One copy, used by every tab.
 */

export const RULE_LABEL: Record<string, string> = {
  name_exact: 'Exact name',
  name_ambiguous: 'Ambiguous name',
  name_probabilistic: 'Probabilistic',
};

export function rulePill(rule: string): string {
  if (rule === 'name_exact') return 'good';
  if (rule === 'name_ambiguous') return 'watch';
  return 'info';
}

/** The matcher's evidence for one suggestion, as one readable line. */
export function featureLine(f: Record<string, unknown>): string {
  const bits: string[] = [];
  if (f.norm_name) bits.push(String(f.source_name ?? f.norm_name));
  if (f.master_name && f.master_name !== f.norm_name) bits.push(`~ ${f.master_name}`);
  if (f.similarity != null) bits.push(`similarity ${Number(f.similarity).toFixed(2)}`);
  if (f.n_targets != null && Number(f.n_targets) > 1) bits.push(`${f.n_targets} possible masters`);
  if (f.left_woreda) bits.push(`woreda ${f.left_woreda}`);
  if (f.left_zone) bits.push(`zone ${f.left_zone}`);
  if (f.distance_km != null) bits.push(`${f.distance_km} km`);
  return bits.join(' · ');
}

/** A failed read: an alert note with a retry, or; when the gateway answered
 *  403: the access sentence with no retry. The raw error stays on hover. */
export function LoadError({ what, err, retry }: { what: string; err: string; retry: () => void }) {
  const denied = err.startsWith('403');
  return (
    <div className="rv-note rv-note-alert" role="alert" title={err}>
      <svg className="ico ico-sm"><use href="#i-alert" /></svg>
      <span className="rv-note-txt">
        {denied ? `You do not have access to ${what}.` : `Could not load ${what}.`}
      </span>
      {!denied && <button className="btn btn-sm btn-ghost" onClick={retry}>Try again</button>}
    </div>
  );
}

/** A failed write (decide / revert / publish): never shown as a failed load. */
export function ActionError({ text, err }: { text: string; err: string }) {
  return (
    <div className="rv-note rv-note-alert" role="alert" title={err}>
      <svg className="ico ico-sm"><use href="#i-alert" /></svg>
      <span className="rv-note-txt">{text}</span>
    </div>
  );
}

export function fmtNum(n: number | null | undefined): string {
  return n == null ? 'n/a' : n.toLocaleString();
}

/** A date without the time, for "last changed" columns. */
export function fmtDay(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : 'n/a';
}

export function fmtWhen(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'n/a';
}

/** A confirmed change: the review kit's good note, dismissible. */
export function DoneNote({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div className="rv-note rv-note-good" role="status">
      <svg className="ico ico-sm"><use href="#i-check-circle" /></svg>
      <span className="rv-note-txt">{text}</span>
      <button className="rv-note-dismiss" aria-label="Dismiss" onClick={onClose}>
        <svg className="ico ico-sm"><use href="#i-x" /></svg>
      </button>
    </div>
  );
}

/** An empty list: the review kit's empty block (icon + one line). */
export function Empty({ text }: { text: string }) {
  return (
    <div className="rv-empty">
      <svg className="ico"><use href="#i-check-circle" /></svg>
      <h4>{text}</h4>
    </div>
  );
}
