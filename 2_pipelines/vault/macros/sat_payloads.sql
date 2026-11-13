{#-
    sat_payloads: the single source of truth for every satellite payload and
    its hashdiff, shared by the stage view (hashdiff) and the sat model
    (payload) so the two cannot diverge.

    Volatile-timestamp policy (vault-wide, applied symmetrically)
    1. Every descriptive / measure column in a sat's payload must be part
       of the stage hashdiff: a payload column missing from the hashdiff
       means changes to it never produce a new sat version (silent history
       loss).
    2. The only exception: pure audit timestamps that advance on every
       upstream write without carrying business meaning (`created`,
       `last_updated`, `modified_date`, `meta_last_updated`). They stay in
       the payload (audit value) but are excluded from the hashdiff via
       the `volatile` list below, so a touch-only upstream write does not
       fabricate a new sat version.
    3. Keys are not descriptive attributes: columns hashed into the parent
       hub/link hash key (incl. dependent-child keys such as
       lnk_mbrana_stock_event's soh_log_id and lnk_hcmis_vaccine_event's
       date, both stored on their link via src_extra_columns) never
       appear in a payload.

    Usage:
        stage view:  {{ automate_dv.hash(columns=sat_hashdiff('mfr_facility'),
                                         alias='hd_mfr_facility', is_hashdiff=true) }}
        sat model:   src_payload=sat_payload('mfr_facility')

    The hashdiff is derived as payload-minus-volatile in payload order:
    column order is part of the hash contract, so never reorder a payload
    list casually. scripts/check-sat-payload-alignment.py asserts every
    sat/stage pair consumes this spec with the same key.
-#}

{% macro _sat_payload_spec() %}
{%- do return({
    'climate_week': {
        'payload': ['week_start', 'rainfall_mm', 'temp_celsius', 'humidity_pct'],
        'volatile': [],
    },

    'dhis2_category_combo': {
        'payload': ['name', 'code', 'last_updated'],
        'volatile': ['last_updated'],
    },
    'dhis2_category_option_combo': {
        'payload': ['name', 'code', 'category_combo_id', 'last_updated'],
        'volatile': ['last_updated'],
    },
    'dhis2_data_element': {
        'payload': ['name', 'code', 'short_name', 'value_type', 'aggregation_type',
                    'domain_type', 'category_combo_id', 'last_updated'],
        'volatile': ['last_updated'],
    },
    'dhis2_data_set': {
        'payload': ['name', 'code', 'period_type', 'category_combo_id', 'last_updated'],
        'volatile': ['last_updated'],
    },
    'dhis2_data_value': {
        'payload': ['value', 'stored_by', 'created'],
        'volatile': ['created'],
    },
    'dhis2_org_unit': {
        'payload': ['name', 'code', 'short_name', 'level', 'path', 'parent_id',
                    'opening_date', 'closed_date', 'last_updated'],
        'volatile': ['last_updated'],
    },

    'ethioemr_encounter': {
        'payload': ['status', 'class_system', 'class_code', 'class_display',
                    'period_start', 'period_end',
                    'service_provider_reference', 'service_provider_display',
                    'part_of_reference', 'meta_version_id', 'meta_last_updated'],
        'volatile': ['meta_last_updated'],
    },
    'ethioemr_observation': {
        'payload': ['status', 'code_text', 'effective_dt', 'effective_period_start',
                    'effective_period_end', 'issued',
                    'value_quantity_value', 'value_quantity_unit',
                    'value_quantity_system', 'value_quantity_code',
                    'value_codeable_concept_text', 'value_string', 'value_date_time',
                    'method_text', 'meta_version_id', 'meta_last_updated'],
        'volatile': ['meta_last_updated'],
    },
    'ethioemr_patient': {
        'payload': ['gender', 'birth_date', 'meta_source', 'meta_version_id',
                    'meta_last_updated'],
        'volatile': ['meta_last_updated'],
    },

    'hcmis_commodity': {
        'payload': ['name', 'description', 'commodity_type_id', 'is_active',
                    'is_vaccine', 'is_nps', 'prescription_type', 'rowguid'],
        'volatile': [],
    },
    'hcmis_date': {
        'payload': ['full_date', 'day_of_week', 'day_name_of_week', 'day_of_month',
                    'day_of_year', 'weekday_weekend', 'week_of_year', 'month_name',
                    'month_of_year', 'calendar_quarter', 'calendar_year',
                    'fiscal_year', 'fiscal_quarter', 'fiscal_month_of_year',
                    'eth_year', 'eth_month', 'eth_day_of_month',
                    'eth_day_name_of_week', 'eth_month_name', 'eth_full_date',
                    'is_holiday', 'is_last_day_of_month'],
        'volatile': [],
    },
    'hcmis_facility': {
        'payload': ['name', 'description', 'environment_id', 'rowguid'],
        'volatile': [],
    },
    'hcmis_item': {
        'payload': ['full_item_name'],
        'volatile': [],
    },
    'hcmis_environment': {
        'payload': ['name', 'environment_code', 'environment_group_code', 'region_id'],
        'volatile': [],
    },
    'hcmis_stock_event': {
        'payload': ['mos12', 'amc12', 'soh', 'soh_amount',
                    'received', 'amount_received', 'bb_received', 'amount_bb_received',
                    'standard_issued', 'amount_standard_issued', 'revenue_standard_issued',
                    'facility_issued', 'amount_facility_issued', 'revenue_facility_issued',
                    'center_hub_issued', 'amount_center_hub_issued', 'revenue_center_hub_issued',
                    'expired', 'amount_expired', 'damaged', 'amount_damaged',
                    'standard_quantity_requested', 'stock_status', 'fiscal_year'],
        'volatile': [],
    },
    'hcmis_vaccine_event': {
        'payload': ['received', 'issued', 'expired', 'soh'],
        'volatile': [],
    },
    'hcmis_purchase_order': {
        'payload': ['po_number', 'item_id', 'environment_id', 'supplier_name',
                    'quantity', 'unit_cost', 'order_date', 'expected_delivery_date',
                    'status'],
        'volatile': [],
    },

    'hcmis_dispatch_hub': {
        'payload': ['name', 'short_name', 'zone', 'lng', 'lat', 'is_hub', 'vehicles'],
        'volatile': [],
    },
    'hcmis_dispatch_facility': {
        'payload': ['name', 'region', 'facility_type', 'lng', 'lat',
                    'line_count', 'store_cap'],
        'volatile': [],
    },
    'hcmis_dispatch_period': {
        'payload': ['label', 'sort_order', 'movements', 'units', 'runs', 'facilities',
                    'avg_fill', 'overflow_legs', 'min_months', 'max_months',
                    'uncovered_needs', 'at_risk_facilities', 'optimized_km', 'baseline_km',
                    'km_saved', 'km_saved_pct', 'optimized_cost', 'baseline_cost',
                    'cost_saved', 'cost_saved_pct', 'vehicles_used', 'naive_vehicles',
                    'vehicles_saved'],
        'volatile': [],
    },
    'hcmis_dispatch_run': {
        'payload': ['period_id', 'vehicle_type', 'capacity_units', 'origin_hub_id',
                    'total_road_km', 'total_units', 'fill_rate', 'cost_total',
                    'cost_per_unit', 'km_saved_vs_baseline', 'depart', 'eta', 'status',
                    'stops_json', 'path_json'],
        'volatile': [],
    },
    'hcmis_dispatch': {
        'payload': ['drug', 'drug_short', 'commodity_key', 'units', 'amc', 'mos',
                    'days_to_stockout', 'priority', 'lane_km', 'cost_units',
                    'sequence_index', 'vehicle_run_id', 'fill_rate', 'nearest_hub_id',
                    'alt_hub_id', 'reason', 'assign_delta_km', 'assign_delta_cost',
                    'baseline_hub_id', 'baseline_km', 'planned', 'repaired', 'capped',
                    'why_json'],
        'volatile': [],
    },

    'mbrana_commodity': {
        'payload': ['commodity_code', 'commodity_name'],
        'volatile': [],
    },
    'mbrana_facility': {
        'payload': ['name', 'description', 'phone', 'woreda', 'town', 'kebele',
                    'ru_type', 'ownership', 'zone', 'administrative_unit_id',
                    'operational_status', 'latitude', 'longitude', 'active',
                    'in_process', 'alternate_name', 'reporting_group_id',
                    'environment_id', 'modified_date'],
        'volatile': ['modified_date'],
    },
    'mbrana_region': {
        'payload': ['region_name', 'region_code', 'p_code', 'latitude', 'longitude',
                    'is_active', 'modified_date'],
        'volatile': ['modified_date'],
    },
    'mbrana_stock_event': {
        'payload': ['quantity', 'date'],
        'volatile': [],
    },
    'mbrana_ussd_facility': {
        'payload': ['institution_name', 'parent_institution_id'],
        'volatile': [],
    },
    'mbrana_woreda': {
        'payload': ['woreda_name', 'woreda_code', 'p_code', 'latitude', 'longitude',
                    'is_active', 'modified_date'],
        'volatile': ['modified_date'],
    },
    'mbrana_zone': {
        'payload': ['zone_name', 'zone_code', 'p_code', 'latitude', 'longitude',
                    'is_active', 'modified_date'],
        'volatile': ['modified_date'],
    },

    'elmis_hist_facility': {
        'payload': ['name', 'facility_type_code', 'geographic_zone_code',
                    'parent_facility_code', 'catchment_population', 'latitude',
                    'longitude', 'service_delivery_point', 'active', 'enabled',
                    'virtual_facility', 'go_live_date', 'go_down_date'],
        'volatile': [],
    },
    'elmis_hist_zone': {
        'payload': ['name', 'geographic_level_code', 'parent_zone_code',
                    'catchment_population', 'latitude', 'longitude'],
        'volatile': [],
    },
    'elmis_hist_product': {
        'payload': ['primary_name', 'full_name', 'generic_name', 'strength',
                    'product_form_code', 'product_group_code', 'dispensing_unit',
                    'pack_size', 'tracer', 'active', 'full_supply', 'refrigerated'],
        'volatile': [],
    },
    'elmis_hist_programme': {
        'payload': ['name', 'active'],
        'volatile': [],
    },
    'elmis_hist_period': {
        'payload': ['processing_schedule_code', 'name', 'period_start',
                    'period_end', 'number_of_months'],
        'volatile': [],
    },
    'elmis_hist_requisition': {
        'payload': ['status', 'emergency', 'source_application',
                    'requisition_created_at', 'modified_date'],
        'volatile': ['modified_date'],
    },
    'elmis_hist_requisition_line': {
        'payload': ['beginning_balance', 'quantity_received', 'quantity_dispensed',
                    'stock_in_hand', 'quantity_requested', 'calculated_order_quantity',
                    'quantity_approved', 'total_losses_and_adjustments', 'stockout_days',
                    'normalized_consumption', 'amc', 'max_stock_quantity',
                    'packs_to_ship', 'skipped', 'full_supply', 'reporting_days',
                    'line_item_created_at', 'modified_date'],
        'volatile': ['modified_date'],
    },
    'elmis_hist_facility_type': {
        'payload': ['source_id', 'name', 'geographic_level_code',
                    'nominal_max_months', 'nominal_eop', 'active'],
        'volatile': [],
    },
    'elmis_hist_product_group': {
        'payload': ['source_id', 'name'],
        'volatile': [],
    },
    'elmis_hist_product_category': {
        'payload': ['source_id', 'name', 'display_order'],
        'volatile': [],
    },
    'elmis_hist_programme_product': {
        'payload': ['programme_code', 'product_code', 'source_id', 'product_category_code',
                    'doses_per_month', 'active', 'current_price', 'display_order', 'full_supply'],
        'volatile': [],
    },
    'elmis_hist_facility_schedule': {
        'payload': ['facility_code', 'programme_code', 'processing_schedule_code',
                    'source_requisition_group_id', 'source_requisition_group_code',
                    'source_group_member_id', 'source_group_schedule_id',
                    'direct_delivery', 'drop_off_facility_code'],
        'volatile': [],
    },

    'mfr_facility': {
        'payload': ['name', 'status', 'mode', 'operational_status',
                    'facility_type_code', 'facility_type_display',
                    'physical_type_code', 'physical_type_display',
                    'longitude', 'latitude', 'altitude',
                    'address', 'address_raw', 'telecom', 'identifier',
                    'managing_organization_reference',
                    'part_of_reference', 'part_of_display'],
        'volatile': [],
    },
    'mfr_organization': {
        'payload': ['name', 'active', 'alias', 'type_code', 'type_display',
                    'telecom', 'identifier', 'part_of_reference', 'part_of_display',
                    'staff_phys', 'staff_nurse', 'staff_hew'],
        'volatile': [],
    },
    'mfr_device': {
        'payload': ['equipment_type', 'status', 'owner_facility_id'],
        'volatile': [],
    },
}) -%}
{% endmacro %}


{#- Full descriptive payload for a satellite (audit timestamps included). -#}
{% macro sat_payload(sat) %}
{%- do return(_sat_payload_spec()[sat]['payload']) -%}
{% endmacro %}


{#- Hashdiff columns for a satellite = payload minus volatile audit
    timestamps, in payload order. -#}
{% macro sat_hashdiff(sat) %}
{%- set spec = _sat_payload_spec()[sat] -%}
{%- set cols = [] -%}
{%- for col in spec['payload'] if col not in spec['volatile'] -%}
    {%- do cols.append(col) -%}
{%- endfor -%}
{%- do return(cols) -%}
{% endmacro %}
