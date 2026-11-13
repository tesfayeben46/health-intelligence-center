/**
 * Models and datasets (page id admin/models-datasets): the admin's model &
 * dataset console, with two sections:
 *
 *   · Model fleet (rendered by MLHealth): drift +
 *     performance monitoring of the deployed models.
 *   · Graduation review queue (rendered by Review):
 *     submissions (models and datasets) awaiting peer-review sign-off before
 *     they publish to BI.
 *
 * This module only composes the two under one page and one nav entry.
 */
import MLHealth from './MLHealth';
import Review from './Review';

function ModelsDatasets() {
  return (
    <>
      <MLHealth />
      <div className="md-divider" />
      <Review />
    </>
  );
}

/* widget-module registration (admin catalog runtime)
   One complex surface is one grantable widget: this module is the page's single catalog widget. */
import type { AdminWidget } from '../../runtime/types';
const widget: AdminWidget = {
  id: 'adm-models-datasets',
  Component: ModelsDatasets,
};
export default widget;
