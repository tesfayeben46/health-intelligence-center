/*
    AutomateDV stage model: pre-computes every hash key and hash diff that
    the dhis2_data_value hub/link/sat models will consume.

    Why a view here (not table): the source is already a typed snapshot in
    nhdw_landing; a query-time projection that adds hash columns is enough.
    The downstream hub/link/sat models are incremental tables.

    hk_dhis2_data_value is hashed over the same 5-column natural key the
    staging layer dedupes on (2_pipelines/staging/models/dhis2/
    stg_dhis2__data_value_sets.sql): one grain, declared once. data_set is
    not part of the value's identity: a re-ingest delivering
    the same 5-tuple under a different data_set must not split the sat
    history across two keys. data_set BKs still feed hub_dhis2_data_set
    (via v_stg_dhis2__data_sets_from_facts); model data_element<->data_set
    membership as its own link if a mart ever needs it.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT * FROM {{ source('landing', 'stg_dhis2__data_value_sets') }}
)
SELECT *,
       'DHIS2.data_value_sets' AS record_source,
       {{ automate_dv.hash(columns=['data_element'],                              alias='hk_dhis2_data_element')               }},
       {{ automate_dv.hash(columns=['period'],                                    alias='hk_dhis2_period')                     }},
       {{ automate_dv.hash(columns=['org_unit'],                                  alias='hk_dhis2_org_unit')                   }},
       {{ automate_dv.hash(columns=['category_option_combo'],                     alias='hk_dhis2_category_option_combo')      }},
       {{ automate_dv.hash(columns=['attribute_option_combo'],                    alias='hk_dhis2_attribute_option_combo')     }},
       {{ automate_dv.hash(columns=['data_set'],                                  alias='hk_dhis2_data_set')                   }},
       {{ automate_dv.hash(columns=['data_element','period','org_unit','category_option_combo','attribute_option_combo'],
                          alias='hk_dhis2_data_value') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_data_value'), alias='hd_dhis2_data_value', is_hashdiff=true) }}
FROM src
