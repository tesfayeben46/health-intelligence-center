/**
 * Admin dashboard · modular widget runtime: shared types.
 *
 * Same catalog-widget pattern as the public dashboard's runtime/types.ts: a
 * page is a composition of self-contained widget modules discovered from the
 * filesystem (runtime/registry.ts) and composed from the caller's resolved
 * catalog spec (/gw/permissions/dashboard/admin/spec, see
 * runtime/PageRenderer.tsx). App.tsx imports no widget directly; the shell
 * keeps only furniture (Sidebar, Topbar, Toast, AuthSplash).
 *
 * One module is one catalog widget: a complex surface that works as a unit is
 * one grantable widget, in the same dashboard, page, widget model as every other
 * dashboard. Ids are 1:1 with ops_perm.widget rows.
 */
import type { FC } from 'react';

/**
 * The single surface a widget module is given. Kept thin: widgets use
 * their own hooks (useAdminApp, useAuth, api/*); the host only carries
 * PageRenderer plumbing.
 */
export interface AdminHost {
  /** Active page id: the adminApp store's `active` (e.g. 'infra', 'dq'). */
  active: string;
  /** Navigate to a page (same setter the Sidebar uses). */
  setActive: (id: string) => void;
}

/** A self-contained widget module (default export of widgets/<id>/index.tsx). */
export type AdminWidget = {
  /** Catalog widget id; must equal the folder name (e.g. 'adm-topology'). */
  id: string;
  /** The React component; receives the shared host and nothing else. */
  Component: FC<{ host: AdminHost }>;
};
