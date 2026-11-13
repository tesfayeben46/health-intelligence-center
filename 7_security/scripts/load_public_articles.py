#!/usr/bin/env python3
"""Seed the public-dashboard newsroom.

Idempotent upsert of bundle/seed-pack/app/articles/articles.yaml into
ops_governance.public_article (managed_by='seed'). Follows the same
reconciliation contract as the other YAML loaders: seed-origin rows the YAML no
longer declares are DELETE-synced; runtime-authored rows (managed_by='runtime')
are never touched. Run as a K8s Job (python:3.12-slim) by seed-app-security.sh.

Env: POSTGRES_HOST/PORT/USER, PGPASSWORD, OPS_DB_NAME (default nhdw_ops).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
import yaml

DDL = """
CREATE TABLE IF NOT EXISTS ops_governance.public_article (
    id            text PRIMARY KEY,
    slug          text NOT NULL UNIQUE,
    category      text NOT NULL,
    author        text NOT NULL,
    title         text NOT NULL,
    summary       text NOT NULL,
    body_html     text NOT NULL,
    tags          text[] NOT NULL DEFAULT '{}',
    published_at  date NOT NULL,
    linked_widget text,
    managed_by    text NOT NULL DEFAULT 'seed',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT ON ops_governance.public_article TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.public_article TO ops_writer;
    END IF;
END $$;
"""

UPSERT = """
INSERT INTO ops_governance.public_article
    (id, slug, category, author, title, summary, body_html, tags,
     published_at, linked_widget, managed_by, updated_at)
VALUES (%(id)s, %(slug)s, %(category)s, %(author)s, %(title)s, %(summary)s,
        %(body)s, %(tags)s, %(date)s, %(linked_widget)s, 'seed', now())
ON CONFLICT (id) DO UPDATE SET
    slug=EXCLUDED.slug, category=EXCLUDED.category, author=EXCLUDED.author,
    title=EXCLUDED.title, summary=EXCLUDED.summary, body_html=EXCLUDED.body_html,
    tags=EXCLUDED.tags, published_at=EXCLUDED.published_at,
    linked_widget=EXCLUDED.linked_widget, updated_at=now()
WHERE ops_governance.public_article.managed_by = 'seed';
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", default=os.environ.get(
        "NHDW_SEED_DIR", "/opt/seed"))
    ap.add_argument("--pg-host", default=os.environ.get("POSTGRES_HOST", "localhost"))
    ap.add_argument("--pg-port", default=os.environ.get("POSTGRES_PORT", "5432"))
    ap.add_argument("--pg-user", default=os.environ.get("POSTGRES_USER", "postgres"))
    ap.add_argument("--pg-db",   default=os.environ.get("OPS_DB_NAME", "nhdw_ops"))
    args = ap.parse_args()

    path = Path(args.seed_dir) / "articles.yaml"
    if not path.exists():
        # also accept the articles/ subdir layout
        alt = Path(args.seed_dir) / "articles" / "articles.yaml"
        path = alt if alt.exists() else path
    if not path.exists():
        print(f"[articles] no articles.yaml under {args.seed_dir}; nothing to seed")
        return 0

    doc = yaml.safe_load(path.read_text()) or {}
    articles = doc.get("articles") or []
    print(f"[articles] loaded {len(articles)} articles from {path}")

    conn_kwargs = dict(host=args.pg_host, port=args.pg_port, user=args.pg_user,
                       dbname=args.pg_db, password=os.environ.get("PGPASSWORD"))
    with psycopg.connect(**conn_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            seeded_ids = []
            for a in articles:
                rec = {
                    "id":       a["id"],
                    "slug":     a.get("slug") or a["id"],
                    "category": a["category"],
                    "author":   a["author"],
                    "title":    a["title"],
                    "summary":  a["summary"].strip(),
                    "body":     a["body"].strip(),
                    "tags":     a.get("tags") or [],
                    "date":     str(a["date"]),
                    "linked_widget": a.get("linked_widget"),
                }
                cur.execute(UPSERT, rec)
                seeded_ids.append(rec["id"])
            # reconcile: drop seed-origin rows the YAML no longer declares
            cur.execute(
                "DELETE FROM ops_governance.public_article "
                "WHERE managed_by='seed' AND NOT (id = ANY(%s))", (seeded_ids,))
            deleted = cur.rowcount
        conn.commit()
    print(f"[articles] ok upserted {len(articles)}, pruned {deleted} stale seed rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
