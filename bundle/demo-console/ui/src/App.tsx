import { useEffect, useState } from 'react';
import { fetchPages, fetchScenarios, fetchCredentials,
         type Scenario, type DemoPage, type Credential } from './api/demo';
import Sidebar from './sections/Sidebar';
import PageView from './sections/PageView';
import CredentialsTable from './sections/CredentialsTable';
import SystemConsole from './sections/SystemConsole';

export type Section =
  | { kind: 'page'; pageId: string }
  | { kind: 'credentials' }
  | { kind: 'console' };

export default function App() {
  const [pages, setPages] = useState<DemoPage[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  // Default to the first page.
  const [section, setSection] = useState<Section | null>(null);

  useEffect(() => {
    Promise.all([fetchPages(), fetchScenarios(), fetchCredentials()])
      .then(([p, s, c]) => {
        setPages(p.pages);
        setScenarios(s.scenarios);
        setCredentials(c.credentials);
        if (!section && p.pages.length) {
          setSection({ kind: 'page', pageId: p.pages[0].id });
        }
      })
      .catch((e) => console.warn('demo load failed', e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="layout">
      <Sidebar
        section={section}
        pages={pages}
        onSelect={(s) => setSection(s)}
      />
      <div className="main">
        <div className="content">
          {section?.kind === 'page' && (
            <PageView
              page={pages.find((p) => p.id === section.pageId) || null}
              scenarios={scenarios.filter((s) => s.page === section.pageId)}
              credentials={credentials}
            />
          )}
          {section?.kind === 'credentials' && (
            <CredentialsTable credentials={credentials} />
          )}
          {section?.kind === 'console' && <SystemConsole />}
        </div>
      </div>
    </div>
  );
}
