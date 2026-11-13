/**
 * Linked-toast: shown when arriving from the public dashboard's access
 * request form.
 */
import { useAdminApp } from '../useAdminApp';

export default function Toast() {
  const { state, app, closeToast, setActive } = useAdminApp() as any;
  if (!state.showLinkedToast) return null;
  return (
    <div className="toast">
      <span className="toast-close" onClick={closeToast}>
        <svg className="ico"><use href="#i-x" /></svg>
      </span>
      <div className="toast-title">
        <svg className="ico"><use href="#i-bell" /></svg> New access request from public dashboard
      </div>
      <div className="toast-body">
        Request <strong>{state.newRequestId}</strong> just landed in the queue.
        {' '}
        {/* access requests live on the Users and Access page */}
        <a href="#users-access" onClick={(e) => { e.preventDefault(); setActive('users-access'); }} style={{ color: 'var(--navy-700)' }}>
          View <svg className="ico"><use href="#i-arrow-right" /></svg>
        </a>
      </div>
    </div>
  );
}
