import type { WidgetDef } from '../../runtime/types';
import { createRoot } from 'react-dom/client';
import { createElement } from 'react';
import { CTMovement } from '../../ct/mount';

// Supply Control Tower component mounted as an internal-BI widget: a React
// island inside the widget's DOM slot, unmounted by the returned cleanup.
const def: WidgetDef = {
  id: 'ct-movement',
  frameless: true,
  render(ctx) {
    const root = createRoot(ctx.el);
    root.render(createElement(CTMovement));
    return () => { try { root.unmount(); } catch { /* idempotent */ } };
  },
};
export default def;
