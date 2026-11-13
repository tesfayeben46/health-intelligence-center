/**
 * Demo control room sidebar, the same shape as the admin dashboard's:
 * brand block at top, h6 group headings, link rows for each demo
 * family + a "Reference" group for credentials. Page count appears as
 * a small badge on the right.
 */
import type { Section } from '../App';
import type { DemoPage } from '../api/demo';

export default function Sidebar({
  section, pages, onSelect,
}: {
  section: Section | null;
  pages: DemoPage[];
  onSelect: (s: Section) => void;
}) {
  const activePageId =
    section?.kind === 'page' ? section.pageId : null;

  return (
    <aside className="admin-side">
      <div className="admin-sidehead">
        <a href="#" onClick={(e) => { e.preventDefault(); if (pages[0]) onSelect({ kind: 'page', pageId: pages[0].id }); }}>
          <img className="sidehead-logo" src="/static/assets/logo-fmoh.png" alt="" />
          <span className="sidehead-text">
            <span className="title">NHDW</span>
            <span className="subtitle">Demo control room</span>
          </span>
        </a>
      </div>

      <div>
        <h6>System</h6>
        <a className={section?.kind === 'console' ? 'active' : undefined}
           title="Spin the whole system up/down with live progress"
           onClick={() => onSelect({ kind: 'console' })}>
          Console
        </a>

        <h6>Demo families</h6>
        {pages.map((p) => (
          <a key={p.id}
             className={activePageId === p.id ? 'active' : undefined}
             title={p.description}
             onClick={() => onSelect({ kind: 'page', pageId: p.id })}>
            <span className="side-row">
              <span>{p.title}</span>
              {p.scenario_count > 0 && <span className="badge">{p.scenario_count}</span>}
            </span>
          </a>
        ))}
      </div>

      <span className="admin-side-spacer"></span>

      <h6>Reference</h6>
      <a className={section?.kind === 'credentials' ? 'active' : undefined}
         onClick={() => onSelect({ kind: 'credentials' })}>
        Credentials
      </a>
    </aside>
  );
}
