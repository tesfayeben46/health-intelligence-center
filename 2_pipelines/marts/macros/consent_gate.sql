{#
    consent_gate: wraps a PHI-touching source with an anti-join against
    the consent-revocation relation. Any row whose patient_id appears in
    the revocation table (for the given purpose, or NULL = blanket) is
    suppressed at query time.

    Usage:

        SELECT * FROM {{ consent_gate(ref('demo_patient_phi'),
                                       patient_id_col='patient_id',
                                       purpose='public-health') }}

    `revocation_relation` defaults to the declared
    source('nhdw_landing', 'consent_revocations') (so lineage sees the
    dependency); pass a different relation to gate against another
    revocation surface, such as a view over a FHIR Consent resource,
    without touching the call sites.
#}

{% macro consent_gate(source_relation, patient_id_col='patient_id',
                      purpose=none, revocation_relation=none) -%}
    {%- set revocations = revocation_relation
            if revocation_relation is not none
            else source('nhdw_landing', 'consent_revocations') -%}
    (
        SELECT s.*
        FROM   {{ source_relation }} s
        WHERE  NOT EXISTS (
            SELECT 1
            FROM   {{ revocations }} r
            WHERE  r.patient_id = s.{{ patient_id_col }}
              {%- if purpose %}
              AND (r.purpose IS NULL OR r.purpose = {{ "'" ~ purpose ~ "'" }})
              {%- else %}
              AND r.purpose IS NULL          -- only blanket revocations apply
              {%- endif %}
        )
    )
{%- endmacro %}
