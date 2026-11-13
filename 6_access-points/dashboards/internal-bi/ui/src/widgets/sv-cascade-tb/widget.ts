/** TB care cascade: bi_service.mart_programme_cascade (programme_id='TB'). */
import type { WidgetDef } from '../../runtime/types';
import { renderCascade } from '../_shared/cascade';

const def: WidgetDef = { id: 'sv-cascade-tb', render: renderCascade };
export default def;
