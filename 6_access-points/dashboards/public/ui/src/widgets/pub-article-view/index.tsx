/**
 * Widget pub-article-view: article detail view.
 *
 * Reads the open article from host.currentArticle and setPage from the host.
 * Renders nothing when no article is selected (navigation always sets the
 * article first).
 */
import type { PublicWidget, PublicHost } from '../../runtime/types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "14 Jun 2026" from an ISO date; falls back to the raw value. */
function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : iso;
}

function ArticleView({ host }: { host: PublicHost }) {
  const { currentArticle: article, setPage } = host;
  if (!article) return null;

  return (
    <section className="page-section page-section--article">
      <div className="rd-shell">
        <button className="rd-back" onClick={() => setPage('articles')}>
          <svg className="ico" style={{ transform: 'rotate(180deg)' }}><use href="#i-arrow-right" /></svg>
          Back to all articles
        </button>

        <div className="rd-kicker">
          <span className="rd-cat">{article.category}</span>
          <span className="sep">·</span>
          <span className="rd-date">{fmtDate(article.date)}</span>
          <span className="sep">·</span>
          <span className="rd-author">{article.author}</span>
        </div>

        <h1 className="rd-title">{article.title}</h1>
        {article.summary && <p className="rd-standfirst">{article.summary}</p>}

        <div className="rd-prose" dangerouslySetInnerHTML={{ __html: article.body }} />

        {article.tags?.length > 0 && (
          <div className="rd-tags">
            <span className="rd-tags-lbl">Filed under</span>
            {article.tags.map(t => <span key={t} className="rd-tag">{t}</span>)}
          </div>
        )}

        <div className="rd-foot">
          <button onClick={() => setPage('articles')}>
            <svg className="ico" style={{ transform: 'rotate(180deg)' }}><use href="#i-arrow-right" /></svg>
            All articles
          </button>
          <button onClick={() => setPage('home')}>Home</button>
        </div>
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-article-view', Component: ArticleView };
export default widget;
