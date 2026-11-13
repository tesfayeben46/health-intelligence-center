/**
 * Admin dashboard · widget registry.
 *
 * Filesystem-discovered, like the public dashboard's runtime/registry.ts: every
 * `widgets/<id>/index.tsx` default-exports an AdminWidget, and this glob eagerly
 * imports them all. Adding a widget means adding a folder; nothing here or in
 * App.tsx needs editing. By convention the folder name equals the registered
 * id, and one module is one catalog widget.
 */
import type { AdminWidget } from './types';

const modules = import.meta.glob<{ default: AdminWidget }>(
  '../widgets/*/index.tsx',
  { eager: true },
);

const REGISTRY: Record<string, AdminWidget> = {};

for (const path in modules) {
  const widget = modules[path]?.default;
  if (!widget || !widget.id) {
    // A widget folder without a default AdminWidget export: report it rather
    // than silently dropping a page.
    console.error(`[admin-runtime] ${path} has no default AdminWidget export`);
    continue;
  }
  if (REGISTRY[widget.id]) {
    console.error(`[admin-runtime] duplicate widget id "${widget.id}" (${path})`);
  }
  REGISTRY[widget.id] = widget;
}

/** Look up a widget module by catalog id (undefined if not registered). */
export function getWidget(id: string): AdminWidget | undefined {
  return REGISTRY[id];
}

/** All registered widget ids: for diagnostics / the missing-id path. */
export function registeredIds(): string[] {
  return Object.keys(REGISTRY);
}
