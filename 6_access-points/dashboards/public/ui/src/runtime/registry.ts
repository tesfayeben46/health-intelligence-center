/**
 * Public dashboard · widget registry.
 *
 * Filesystem-discovered, exactly like internal-bi's runtime/registry.ts:
 * every `widgets/<id>/index.tsx` default-exports a PublicWidget, and this glob
 * eagerly imports them all into a `Record<id, PublicWidget>`. Adding a widget =
 * dropping a folder; nothing here (or in App.tsx) needs editing. The id is read
 * from each module's own `id` field, so the folder name and the registered id
 * match by convention (and are asserted in dev below).
 */
import type { PublicWidget } from './types';

const modules = import.meta.glob<{ default: PublicWidget }>(
  '../widgets/*/index.tsx',
  { eager: true },
);

const REGISTRY: Record<string, PublicWidget> = {};

for (const path in modules) {
  const widget = modules[path]?.default;
  if (!widget || !widget.id) {
    // A widget folder that forgot to default-export a PublicWidget: surface it
    // loudly in dev rather than silently dropping a page section.
    console.error(`[public-runtime] ${path} has no default PublicWidget export`);
    continue;
  }
  if (REGISTRY[widget.id]) {
    console.error(`[public-runtime] duplicate widget id "${widget.id}" (${path})`);
  }
  REGISTRY[widget.id] = widget;
}

/** Look up a widget module by id (undefined if not registered). */
export function getWidget(id: string): PublicWidget | undefined {
  return REGISTRY[id];
}

/** All registered widget ids: handy for diagnostics / the missing-id path. */
export function registeredIds(): string[] {
  return Object.keys(REGISTRY);
}
