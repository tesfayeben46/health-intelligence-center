/**
 * Widget pub-newsroom: the three-card news grid on the home page.
 *
 * Uses the same card style as the Articles grid (widgets/pub-articles). The
 * formatting helpers are copied, not imported, because widgets share no modules.
 * Data via useArticles; reads setPage/setArticle from the host.
 */
import { type Article } from '../../data/articles';
import { useProfile } from '../../hooks/useProfile';
import { useArticles } from '../../hooks/useArticles';
import type { PublicWidget, PublicHost } from '../../runtime/types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "14 Jun 2026" from an ISO date; falls back to the raw value. */
function fmtDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : iso;
}

/** Plain-text excerpt from an article body (strips HTML). */
function excerpt(html: string, max = 150): string {
  const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').replace(/\s+([.,;:!?])/g, '$1').trim();
  if (text.length <= max) return text;
  return text.slice(0, max).replace(/\s+\S*$/, '') + '…';
}

/** Restrained category tint (class only; colours live in CSS): mirrors pub-articles. */
function catTone(category: string): string {
  const k = category.toLowerCase();
  if (/outbreak|alert|advis|warn/.test(k)) return 'warn';
  if (/phem|surveil|epidem/.test(k)) return 'teal';
  if (/research|study|program/.test(k)) return 'plum';
  return 'navy';
}

function Newsroom({ host }: { host: PublicHost }) {
  const { copy } = useProfile();
  const t = copy.newsroom ?? { title: "What's happening", sub: '' };
  const { setPage, setArticle } = host;
  const open = (a: Article) => { setArticle(a); setPage('article'); };
  const top3 = useArticles().slice(0, 3);

  return (
    <section id="news" className="page-section page-section--news">
      <div className="ins-hdr ins-hdr--news">
        <p className="ins-eyebrow"><span className="ins-rule"></span> Latest · news &amp; bulletins</p>
        <div className="ins-titlebar">
          <h2 className="ins-h2">{t.title}</h2>
          <button className="btn-seeall" onClick={() => setPage('articles')}>
            See all articles <svg className="ico ico-sm"><use href="#i-arrow-right" /></svg>
          </button>
        </div>
        <p className="ins-sub">{t.sub}</p>
      </div>
      <div className="nw-grid">
        {top3.map((a) => (
          <article key={a.id} className="nw-card">
            <button className="nw-card-btn" onClick={() => open(a)}>
              <span className="nw-card-top">
                <span className={`nw-tag nw-tag--${catTone(a.category)}`}>{a.category}</span>
                <span className="nw-date">{fmtDate(a.date)}</span>
              </span>
              <h3 className="nw-card-title">{a.title}</h3>
              <p className="nw-card-dek">{a.summary || excerpt(a.body)}</p>
              <span className="nw-card-read">Read more <svg className="ico"><use href="#i-arrow-right" /></svg></span>
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-newsroom', Component: Newsroom };
export default widget;
