/**
 * Widget registry: build-time discovery of every `src/widgets/<id>/widget.ts`.
 *
 * The shell renders whatever the catalog places on a page; this registry maps a
 * catalog widget id to render code. A new widget (folder plus catalog row)
 * needs no shell edit. A catalog id with no matching folder renders a "no
 * renderer" placeholder rather than being dropped, so drift is visible.
 *
 * Vite's import.meta.glob with eager:true inlines every match at build time, so
 * the bundle is static (no dynamic network import).
 */
import type { WidgetDef } from './types';

const modules = import.meta.glob<{ default: WidgetDef }>(
  '../widgets/**/widget.ts',
  { eager: true },
);

const REGISTRY: Record<string, WidgetDef> = {};

for (const [path, mod] of Object.entries(modules)) {
  const def = (mod as { default?: WidgetDef }).default;
  if (!def || !def.id) {
    console.warn(`[registry] ${path} has no default WidgetDef export: skipped`);
    continue;
  }
  if (REGISTRY[def.id]) {
    console.warn(`[registry] duplicate widget id "${def.id}" (${path}): overwriting`);
  }
  REGISTRY[def.id] = def;
}

export function getWidgetDef(id: string): WidgetDef | undefined {
  return REGISTRY[id];
}

export function registeredIds(): string[] {
  return Object.keys(REGISTRY);
}
