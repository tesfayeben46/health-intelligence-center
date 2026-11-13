/**
 * Modal registry: build-time discovery of every `src/modals/<id>/modal.ts`.
 *
 * The modal counterpart to the widget registry: it turns a modal request
 * (host.openModal(id, props)) into render code. A new modal is a new folder
 * with a default ModalDef export; the shell never hardcodes it. Uses the same
 * static, eager glob as the widget registry.
 *
 * The glob matches `modal.ts` inside per-id folders only. The React modals in
 * this dashboard are flat `*.tsx` files (AlertModal.tsx, NotificationPanel.tsx),
 * so the two modal systems do not collide. In workshop/ui this folder holds
 * ModalDefs only.
 */
import type { ModalDef } from './types';

const modules = import.meta.glob<{ default: ModalDef }>(
  '../modals/**/modal.ts',
  { eager: true },
);

const REGISTRY: Record<string, ModalDef> = {};

for (const [path, mod] of Object.entries(modules)) {
  const def = (mod as { default?: ModalDef }).default;
  if (!def || !def.id) {
    console.warn(`[modalRegistry] ${path} has no default ModalDef export: skipped`);
    continue;
  }
  if (REGISTRY[def.id]) {
    console.warn(`[modalRegistry] duplicate modal id "${def.id}" (${path}): overwriting`);
  }
  REGISTRY[def.id] = def;
}

export function getModalDef(id: string): ModalDef | undefined {
  return REGISTRY[id];
}

export function registeredModalIds(): string[] {
  return Object.keys(REGISTRY);
}
