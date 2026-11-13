/**
 * Public dashboard · modular widget runtime: shared types.
 *
 * Mirrors internal-bi's catalog-widget pattern in the React idiom: the page is
 * Not a tree of hand-imported sections; it is a *composition of self-contained
 * widget modules* discovered from the filesystem (see runtime/registry.ts) and
 * laid out by runtime/pages.ts. Every widget receives a single `host` object:
 * the only channel through which it reads app state (active page, the open
 * article) or drives navigation / modals. No widget imports another widget, and
 * App.tsx imports no widget directly.
 */
import type { FC } from 'react';
import type { Article } from '../data/articles';

/** Navigable pages. Matches the GovBar nav + the original App state machine. */
export type Page = 'home' | 'data' | 'articles' | 'article';

/** Overlay surfaces. `null` = no modal open. */
export type Modal = null | 'dashboards' | 'access';

/**
 * The single surface a widget is given; everything a widget needs from the
 * shell comes from here:
 *   - GovBar/Hero/Newsroom/Articles: active + setPage + setArticle
 *   - ArticleView: currentArticle
 *   - DataCatalogue/GovBar: openModal
 *   - the modals: closeModal (+ openModal for cross-links)
 */
export interface PublicHost {
  active: Page;
  setPage: (p: Page) => void;
  currentArticle: Article | null;
  setArticle: (a: Article | null) => void;
  openModal: (m: Exclude<Modal, null>) => void;
  closeModal: () => void;
}

/** A self-contained widget module (default export of widgets/<id>/index.tsx). */
export type PublicWidget = {
  /** Stable id used by pages.ts / registry lookup, e.g. 'pub-hero'. */
  id: string;
  /** Rendered bare (outside `.pub-content`) when true: e.g. the hero band. */
  fullBleed?: boolean;
  /** The React component; receives the shared host and nothing else. */
  Component: FC<{ host: PublicHost }>;
};
