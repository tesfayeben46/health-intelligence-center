/**
 * Widget pub-articles: the Articles page (page "articles").
 *
 * Masthead, a featured slideshow of the newest articles (prev/next and dots),
 * then a filter bar, a three-column card grid and numbered pagination. Data via
 * useArticles; reads setPage/setArticle from the host.
 */
import { useMemo, useState, useEffect } from 'react';
import { ARTICLE_CATEGORIES, type Article } from '../../data/articles';
import { useArticles } from '../../hooks/useArticles';
import { useProfile } from '../../hooks/useProfile';
import type { PublicWidget, PublicHost } from '../../runtime/types';

type Sort = 'newest' | 'oldest' | 'az';
type Range = 'all' | '30' | '90' | 'year';
const PAGE_SIZE = 6;
const FEATURED = 5;

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

/** Approx reading time in whole minutes from an HTML body (~200 wpm). */
function readMins(html: string): number {
  const words = html.replace(/<[^>]+>/g, ' ').split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}

/** Restrained category tint (class only; colours live in CSS). Kept to at most
 *  three tones: accent teal for surveillance/PHEM, a muted amber for
 *  advisories/outbreaks, slate for everything else. */
function catTone(category: string): string {
  const k = category.toLowerCase();
  if (/outbreak|alert|advis|warn/.test(k)) return 'warn';
  if (/phem|surveil|epidem/.test(k)) return 'teal';
  if (/research|study|program/.test(k)) return 'plum';
  return 'navy';
}

/** Days between an ISO date and today (approximate; used by the date-range filter). */
function ageDays(iso: string): number {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return Number.POSITIVE_INFINITY;
  return (Date.now() - t) / 86_400_000;
}

function Articles({ host }: { host: PublicHost }) {
  const { setPage, setArticle } = host;
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('all');
  const [topic, setTopic] = useState<string>('all');
  const [range, setRange] = useState<Range>('all');
  const [sort, setSort] = useState<Sort>('newest');
  const [page, setPageNum] = useState(0);
  const [featIdx, setFeatIdx] = useState(0);
  const ART = useArticles();
  // Masthead copy from the active profile: so the newsroom intro names the
  // right ministry/country (or stays neutral) instead of hardcoding Ethiopia.
  const { copy } = useProfile();
  const head = copy?.articles_page ?? {};

  // Featured lead = the newest few, independent of the browse filters below.
  const featured = useMemo(
    () => [...ART].sort((a, b) => b.date.localeCompare(a.date)).slice(0, FEATURED),
    [ART]);
  const slideCount = featured.length;
  const feat = featured[featIdx % Math.max(1, slideCount)] || null;
  const goSlide = (i: number) => setFeatIdx((i + slideCount) % Math.max(1, slideCount));

  // Gentle auto-advance through the featured slides; any manual nav resets the
  // timer (featIdx is a dep) and it stops when there is a single slide.
  useEffect(() => {
    if (slideCount < 2) return;
    const id = setInterval(() => setFeatIdx(i => (i + 1) % slideCount), 7000);
    return () => clearInterval(id);
  }, [slideCount, featIdx]);

  const topics = useMemo(
    () => Array.from(new Set(ART.flatMap(a => a.tags.map(t => t.trim()).filter(Boolean)))).sort(),
    [ART]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return ART
      .filter(a => category === 'all' || a.category === category)
      .filter(a => topic === 'all' || a.tags.map(t => t.trim()).includes(topic))
      .filter(a => range === 'all'
                || (range === '30' && ageDays(a.date) <= 30)
                || (range === '90' && ageDays(a.date) <= 90)
                || (range === 'year' && (a.date || '').slice(0, 4) === String(new Date().getFullYear())))
      .filter(a => !q
                || a.title.toLowerCase().includes(q)
                || a.summary.toLowerCase().includes(q)
                || a.body.toLowerCase().includes(q)
                || a.category.toLowerCase().includes(q)
                || a.tags.some(t => t.toLowerCase().includes(q)))
      .sort((a, b) => {
        if (sort === 'newest') return b.date.localeCompare(a.date);
        if (sort === 'oldest') return a.date.localeCompare(b.date);
        if (sort === 'az')     return a.title.localeCompare(b.title);
        return 0;
      });
  }, [ART, query, category, topic, range, sort]);

  // On the default view (no filters, newest-first) the featured items are already
  // surfaced in the lead, so drop them from the list to avoid a duplicate top row.
  // When the reader searches or filters, the list stays complete.
  const isDefault = !query.trim() && category === 'all' && topic === 'all' && range === 'all' && sort === 'newest';
  const listed = useMemo(() => {
    if (!isDefault) return filtered;
    const ids = new Set(featured.map(f => f.id));
    return filtered.filter(a => !ids.has(a.id));
  }, [filtered, featured, isDefault]);

  const totalPages = Math.max(1, Math.ceil(listed.length / PAGE_SIZE));
  const paged = listed.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const open = (a: Article) => { setArticle(a); setPage('article'); };
  const clear = () => { setQuery(''); setCategory('all'); setTopic('all'); setRange('all'); setPageNum(0); };
  const onAnyFilter = () => setPageNum(0);

  // Numbered pager with an ellipsis when there are many pages (1 … n).
  const pageList: (number | '…')[] = useMemo(() => {
    if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i);
    const out: (number | '…')[] = [0];
    const lo = Math.max(1, page - 1), hi = Math.min(totalPages - 2, page + 1);
    if (lo > 1) out.push('…');
    for (let i = lo; i <= hi; i++) out.push(i);
    if (hi < totalPages - 2) out.push('…');
    out.push(totalPages - 1);
    return out;
  }, [totalPages, page]);

  return (
    <section className="nw-page">
      <div className="nw">
        {/* Masthead: same opening as the rest of the site */}
        <header className="nw-head">
          <p className="nw-eyebrow">{head.eyebrow ?? 'Newsroom'}</p>
          <h1 className="nw-title">{head.title ?? 'News and health bulletins'}</h1>
          <p className="nw-sub">
            {head.sub ?? 'Surveillance bulletins, official press releases and public-health '
              + 'advisories, most recent first.'}
          </p>
        </header>

        {/* Featured slideshow: a light card: headline column + a big story icon,
            cycling through the newest few (auto-advance + dots, no dark island) */}
        {feat && (
          <div className="nw-slideshow" aria-roledescription="carousel" aria-label="Featured articles">
            <article className="nw-slide" key={feat.id}>
              <div className="nw-slide-main">
                <p className="nw-slide-kicker">
                  <span className="nw-slide-flag">Featured</span>
                  <span className={`nw-tag nw-tag--${catTone(feat.category)}`}>{feat.category}</span>
                </p>
                <h2 className="nw-slide-h2">
                  <button className="nw-slide-title" onClick={() => open(feat)}>{feat.title}</button>
                </h2>
                <p className="nw-slide-dek">{feat.summary || excerpt(feat.body, 170)}</p>
                <div className="nw-slide-meta">
                  {feat.author && (
                    <span className="nw-slide-by"><svg className="ico"><use href="#i-user" /></svg>{feat.author}</span>
                  )}
                  <span className="nw-slide-fact"><svg className="ico"><use href="#i-calendar" /></svg>{fmtDate(feat.date)}</span>
                  <span className="nw-slide-fact"><svg className="ico"><use href="#i-clock" /></svg>{readMins(feat.body)} min read</span>
                </div>
                <button className="nw-slide-btn" onClick={() => open(feat)}>
                  Read article <svg className="ico"><use href="#i-arrow-right" /></svg>
                </button>
              </div>
            </article>

            {slideCount > 1 && (
              <div className="nw-slide-nav">
                <button className="nw-slide-arrow" onClick={() => goSlide(featIdx - 1)} aria-label="Previous featured article">
                  <svg className="ico nw-slide-arrow-prev"><use href="#i-chevron-right" /></svg>
                </button>
                <span className="nw-slide-dots" role="tablist">
                  {featured.map((f, i) => (
                    <button key={f.id}
                            className={`nw-slide-dot${i === featIdx % slideCount ? ' is-on' : ''}`}
                            aria-label={`Show featured article ${i + 1}`}
                            aria-current={i === featIdx % slideCount ? 'true' : undefined}
                            onClick={() => goSlide(i)} />
                  ))}
                </span>
                <button className="nw-slide-arrow" onClick={() => goSlide(featIdx + 1)} aria-label="Next featured article">
                  <svg className="ico"><use href="#i-chevron-right" /></svg>
                </button>
              </div>
            )}
          </div>
        )}

        {/* List header */}
        <div className="nw-listhdr">
          <h2 className="nw-listhdr-h2">All articles</h2>
          <span className="nw-listhdr-count">{listed.length} {listed.length === 1 ? 'article' : 'articles'}</span>
        </div>

        {/* Unified filter bar */}
        <div className="od-toolbar nw-toolbar">
          <span className="od-search">
            <svg className="ico"><use href="#i-search" /></svg>
            <input type="text" placeholder="Search articles by title or keywords…" aria-label="Search articles"
                   value={query} onChange={(e) => { setQuery(e.target.value); onAnyFilter(); }} />
          </span>
          <div className="od-tools">
            <span className="od-field">
              <svg className="ico"><use href="#i-flag" /></svg>
              <select aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); onAnyFilter(); }}>
                <option value="all">All categories</option>
                {ARTICLE_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </span>
            <span className="od-field">
              <svg className="ico"><use href="#i-filter" /></svg>
              <select aria-label="Topic" value={topic} onChange={(e) => { setTopic(e.target.value); onAnyFilter(); }}>
                <option value="all">All topics</option>
                {topics.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </span>
            <span className="od-field">
              <svg className="ico"><use href="#i-calendar" /></svg>
              <select aria-label="Date range" value={range} onChange={(e) => { setRange(e.target.value as Range); onAnyFilter(); }}>
                <option value="all">Any date</option>
                <option value="30">Last 30 days</option>
                <option value="90">Last 90 days</option>
                <option value="year">This year</option>
              </select>
            </span>
            <span className="od-field">
              <svg className="ico"><use href="#i-arrow-down" /></svg>
              <select aria-label="Sort" value={sort} onChange={(e) => { setSort(e.target.value as Sort); onAnyFilter(); }}>
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
                <option value="az">Title A to Z</option>
              </select>
            </span>
          </div>
        </div>

        {listed.length === 0 ? (
          <div className="od-empty">
            <svg className="ico" style={{ width: 28, height: 28, color: 'var(--ink-4)' }}><use href="#i-search" /></svg>
            <p>No articles match the current filters.</p>
            <button className="od-foot-link" onClick={clear}>Clear filters</button>
          </div>
        ) : (
          <div className="nw-grid">
            {paged.map((a) => (
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
        )}

        {totalPages > 1 && (
          <div className="nw-pager">
            <button className="nw-pg-arrow" disabled={page === 0} onClick={() => setPageNum(p => p - 1)} aria-label="Previous page">‹</button>
            {pageList.map((p, i) => p === '…'
              ? <span key={`e${i}`} className="nw-pg-gap">…</span>
              : <button key={p} className={`nw-pg-num${p === page ? ' is-on' : ''}`} onClick={() => setPageNum(p)}>{p + 1}</button>)}
            <button className="nw-pg-arrow" disabled={page >= totalPages - 1} onClick={() => setPageNum(p => p + 1)} aria-label="Next page">›</button>
          </div>
        )}
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-articles', Component: Articles };
export default widget;
