/**
 * useArticles: fetch the public newsroom from /api/proxy/articles (gateway
 * /api/public/articles, backed by ops_governance.public_article). When the
 * gateway returns nothing usable, falls back to the profile's articles, then
 * to the bundled ARTICLES, so the page always renders.
 */
import { useEffect, useState } from 'react';
import { ARTICLES, type Article } from '../data/articles';
import { useProfile } from './useProfile';

function shape(a: any): Article | null {
  if (!a || !a.id || !a.title) return null;
  return {
    id:       String(a.id),
    date:     String(a.date ?? ''),
    category: String(a.category ?? ''),
    author:   String(a.author ?? ''),
    tags:     Array.isArray(a.tags) ? a.tags.map(String) : [],
    title:    String(a.title),
    summary:  String(a.summary ?? ''),
    body:     String(a.body ?? ''),
  };
}

export function useArticles(): Article[] {
  const [articles, setArticles] = useState<Article[] | null>(null);
  // The bundled ARTICLES are Ethiopian. A profile may carry its own, and a
  // profile that sets `articles: []` gets an empty newsroom rather than
  // another country's bulletins.
  const { copy } = useProfile();
  const fromProfile = copy?.articles as Article[] | undefined;
  useEffect(() => {
    let cancelled = false;
    fetch('/api/proxy/articles?limit=100')
      .then(r => (r.ok ? r.json() : null))
      .then(body => {
        const arr = Array.isArray(body?.articles) ? body.articles : null;
        if (cancelled || !arr) return;
        const mapped = arr.map(shape).filter((a: Article | null): a is Article => a !== null);
        if (mapped.length) setArticles(mapped);
      })
      .catch(() => { /* keep fallback */ });
    return () => { cancelled = true; };
  }, []);
  return articles ?? (Array.isArray(fromProfile) ? fromProfile : ARTICLES);
}
