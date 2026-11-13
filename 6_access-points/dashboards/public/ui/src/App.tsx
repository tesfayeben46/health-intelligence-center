/**
 * Public dashboard root component.
 *
 * App owns the app state (active page, open modal, current article) and builds
 * the single `host` object. It imports no widget directly: the top chrome, the
 * page body, the bottom chrome and the active modal are looked up in the widget
 * registry by id (runtime/registry.ts, runtime/pages.ts). Adding or moving a
 * section is a change to pages.ts plus a widgets/<id>/ folder.
 */
import { useMemo, useState } from 'react';
import type { Page, Modal, PublicHost } from './runtime/types';
import { CHROME_TOP, CHROME_BOTTOM, MODALS } from './runtime/pages';
import PageRenderer, { WidgetSlot } from './runtime/PageRenderer';
import type { Article } from './data/articles';

export type { Page, Modal } from './runtime/types';

export default function App() {
  const [active, setActive] = useState<Page>('home');
  const [modal, setModal] = useState<Modal>(null);
  const [currentArticle, setCurrentArticle] = useState<Article | null>(null);

  const host = useMemo<PublicHost>(() => ({
    active,
    setPage: (p: Page) => {
      setActive(p);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    currentArticle,
    setArticle: (a: Article | null) => setCurrentArticle(a),
    openModal: (m: Exclude<Modal, null>) => setModal(m),
    closeModal: () => setModal(null),
  }), [active, currentArticle]);

  const modalId = modal ? MODALS[modal] : null;

  return (
    <>
      <WidgetSlot id={CHROME_TOP} host={host} />

      <main id="main"></main>

      <PageRenderer host={host} />

      <WidgetSlot id={CHROME_BOTTOM} host={host} />

      {modalId && <WidgetSlot id={modalId} host={host} />}
    </>
  );
}
