/**
 * Public dashboard · page composition.
 *
 * The single source of truth for *what widgets appear where*. Each page names:
 *   - `fullBleed`: widgets rendered bare, edge-to-edge (before the content well)
 *   - `content`:   widgets wrapped together in `<div className="pub-content">`
 * This reproduces the exact DOM the old App.tsx hand-wrote:
 *   home: <Hero/> then .pub-content>(Newsroom, PublicInsights)
 *   data: .pub-content>DataCatalogue
 *   articles: .pub-content>Articles
 *   article: .pub-content>ArticleView
 * Chrome (GovBar/Footer) and the two modals sit outside the page flow.
 */
import type { Page, Modal } from './types';

// pub-insights (DHIS2 coverage) and pub-zm-supply (eLMIS supply) both sit on the
// home page; the active profile's catalog.yaml `disable:` list decides which one
// renders (PageRenderer filters by it): et-v3 disables pub-zm-supply, zm-v1
// disables pub-insights. Same country-switch mechanism internal-bi uses.
export const PAGES: Record<Page, { fullBleed?: string[]; content: string[] }> = {
  home:     { fullBleed: ['pub-hero'], content: ['pub-newsroom', 'pub-insights', 'pub-zm-supply'] },
  data:     { content: ['pub-data-catalogue'] },
  articles: { content: ['pub-articles'] },
  article:  { content: ['pub-article-view'] },
};

/** Persistent chrome, rendered around every page. */
export const CHROME_TOP = 'pub-govbar';
export const CHROME_BOTTOM = 'pub-footer';

/** Modal id per overlay key. */
export const MODALS: Record<Exclude<Modal, null>, string> = {
  dashboards: 'pub-dashboards-modal',
  access:     'pub-access-modal',
};
