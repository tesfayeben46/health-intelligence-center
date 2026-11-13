/**
 * Detail registry: build-time discovery of every `src/details/<kind>/detail.ts`.
 *
 * The master-detail counterpart to the widget registry: it turns a detail route
 * (host.openDetail(kind, id)) into render code. A new detail view is a new
 * folder with a default DetailDef export; no shell edit. Uses the same static,
 * eager glob as the widget registry.
 */
import type { DetailDef } from './types';

const modules = import.meta.glob<{ default: DetailDef }>(
  '../details/**/detail.ts',
  { eager: true },
);

const REGISTRY: Record<string, DetailDef> = {};

for (const [path, mod] of Object.entries(modules)) {
  const def = (mod as { default?: DetailDef }).default;
  if (!def || !def.kind) {
    console.warn(`[detailRegistry] ${path} has no default DetailDef export: skipped`);
    continue;
  }
  if (REGISTRY[def.kind]) {
    console.warn(`[detailRegistry] duplicate detail kind "${def.kind}" (${path}): overwriting`);
  }
  REGISTRY[def.kind] = def;
}

export function getDetailDef(kind: string): DetailDef | undefined {
  return REGISTRY[kind];
}

export function registeredDetailKinds(): string[] {
  return Object.keys(REGISTRY);
}
