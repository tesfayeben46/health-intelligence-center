/**
 * Widget · pub-hero: orbital dot-globe + masthead + CTAs (fullBleed).
 * Globe canvas is wired by hooks/heroGlobe on mount. A darkening gradient at the
 * hero's lower edge (`.hero::after`) settles it into the content surface. Reads
 * setPage from the host.
 */
import { useEffect, useRef } from 'react';
import type { PublicWidget, PublicHost } from '../../runtime/types';
import { heroGlobe } from '../../hooks/heroGlobe';
import { useProfile } from '../../hooks/useProfile';

function Hero({ host }: { host: PublicHost }) {
  const { setPage } = host;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { profile, copy } = useProfile();
  const t = copy.hero;
  const facts = profile.geo_facts;
  const started = useRef(false);

  // Start the globe once the profile has resolved, so it draws the right
  // country the first time rather than drawing Ethiopia and correcting itself.
  // `started` guards against the profile object changing identity on re-render;
  // the globe owns a RAF loop and must never be mounted twice on one canvas.
  useEffect(() => {
    if (!canvasRef.current || started.current) return;
    started.current = true;
    heroGlobe(canvasRef.current, {
      bbox: facts?.bbox,
      centroid: facts?.centroid,
      homeGeo: 'admin1.geojson',
    });
  }, [facts?.bbox, facts?.centroid]);

  return (
    <section className="hero">
      {/* Background composition · ambient grid + orbiting dot-globe */}
      <div className="hero-bg" aria-hidden={true}>
        <div className="hero-grid"></div>
        <div className="hero-art">
          <div className="hero-rings">
            <div className="hr-ring r1"></div>
            <div className="hr-ring r2"></div>
            <div className="hr-ring r3"></div>
            <div className="hr-ring r4"></div>
            <div className="hr-ring r5"></div>
            <div className="hr-ring r6"></div>
            <div className="hr-ring r7"></div>
          </div>
          <canvas id="hero-globe" ref={canvasRef} className="hero-globe"></canvas>
        </div>
      </div>

      {/* Magazine-style top strip · masthead microcopy */}
      <div className="hero-strip hero-strip--top">
        <div className="hero-strip-inner">
          <span className="hs-lhs">
            <span className="hs-key">{t.strip_volume}</span>
            <span className="hs-sep">/</span>
            <span className="hs-key">{t.strip_series}</span>
          </span>
          <span className="hs-rhs">
            <span>{t.strip_owner}</span>
          </span>
        </div>
      </div>

      <div className="hero-inner">
        <p className="hero-eyebrow">
          <span className="hero-rule" aria-hidden={true}></span>
          <span>{t.eyebrow}</span>
        </p>

        <h1 className="hero-h1">
          <span>{t.headline_lead}</span> <span className="h1-accent">{t.headline_accent}</span>
        </h1>
        {t.headline_native
          ? <p className="hero-h1-am" lang={profile.country?.native?.lang || undefined}>{t.headline_native}</p>
          : null}

        <p className="hero-lede">{t.lede}</p>

        {/* Trust ribbon: the governance posture is the credibility, not fine print. */}
        <p className="hero-trust" title={t.trust_title}>
          <svg className="ico ico-sm"><use href="#i-shield-check"/></svg>
          {t.trust}
        </p>

        <div className="hero-actions">
          <button className="btn-cta-primary" onClick={() => setPage('data')}>
            {t.cta_primary}
            <svg className="ico ico-sm"><use href="#i-arrow-right"/></svg>
          </button>
          <button className="btn-cta-secondary" onClick={() => setPage('articles')}>
            {t.cta_secondary}
            <svg className="ico ico-sm"><use href="#i-arrow-right"/></svg>
          </button>
        </div>
      </div>
    </section>
  );
}

const widget: PublicWidget = { id: 'pub-hero', fullBleed: true, Component: Hero };
export default widget;
