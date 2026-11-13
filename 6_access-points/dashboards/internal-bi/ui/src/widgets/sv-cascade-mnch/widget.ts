/** MNCH continuum cascade: bi_service.mart_programme_cascade (programme_id='MNCH'). */
import type { WidgetDef } from '../../runtime/types';
import { renderCascade } from '../_shared/cascade';

const def: WidgetDef = { id: 'sv-cascade-mnch', render: renderCascade };
export default def;
