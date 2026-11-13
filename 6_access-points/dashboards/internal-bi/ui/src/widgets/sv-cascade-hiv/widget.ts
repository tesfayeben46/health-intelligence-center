/** HIV care cascade: bi_service.mart_programme_cascade (programme_id='HIV'). */
import type { WidgetDef } from '../../runtime/types';
import { renderCascade } from '../_shared/cascade';

const def: WidgetDef = { id: 'sv-cascade-hiv', render: renderCascade };
export default def;
