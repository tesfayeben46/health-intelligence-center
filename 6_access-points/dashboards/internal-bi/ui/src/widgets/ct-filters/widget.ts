import type { WidgetDef } from '../../runtime/types';
import { createRoot } from 'react-dom/client';
import { createElement } from 'react';
import { CTFilters } from '../../ct/mount';
import { ensurePlanLoaded } from '../../ct/data/runtime';

// Supply Control Tower component mounted as an internal-BI widget: a React
// island inside the widget's DOM slot, unmounted by the returned cleanup.
// This widget also triggers the Control Tower plan load.
const def: WidgetDef = {
  id: 'ct-filters',
  frameless: true,
  render(ctx) {
    const root = createRoot(ctx.el);
    root.render(createElement(CTFilters));
    ensurePlanLoaded();
    return () => { try { root.unmount(); } catch { /* idempotent */ } };
  },
};
export default def;
