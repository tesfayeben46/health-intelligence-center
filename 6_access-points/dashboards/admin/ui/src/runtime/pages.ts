/**
 * Admin dashboard · fallback page composition.
 *
 * The primary composition source is the caller's resolved catalog spec
 * (/gw/permissions/dashboard/admin/spec), from which PageRenderer renders the
 * active page's granted widget ids. This static map is used only when the spec
 * is not usable (still loading on first paint, network/5xx failure, or the
 * gateway denying the spec). Falling back to the full catalog composition
 * avoids a blank first frame and blank pages when the gateway is unreachable;
 * each widget then shows its own data-fetch error state.
 *
 * Keys are adminApp `active` ids; values mirror the `admin` page catalog
 * (page id = `admin/<key>`, one widget per page, see ../../../catalog/pages/).
 * When the catalog gains a widget, add it here so the fallback stays complete.
 */
export const FALLBACK_PAGE_WIDGETS: Record<string, string[]> = {
  infra:               ['adm-topology'],
  alerts:              ['adm-alerts-routing'],
  dq:                  ['adm-dq'],
  mdm:                 ['adm-mdm-review'],
  classification:      ['adm-classification'],
  'models-datasets':   ['adm-models-datasets'],
  'users-access':      ['adm-users-access'],
  'roles-permissions': ['adm-roles-permissions'],
  sso:                 ['adm-sso-cards'],
  documentation:       ['adm-documentation'],
};
