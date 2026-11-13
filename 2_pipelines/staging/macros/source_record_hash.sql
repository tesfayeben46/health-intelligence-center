{#
   Hash every column of the staging projection as a stable per-row
   fingerprint. Two consumers:
     1. The dedupe window in every stg_ model appends it as the final
        ORDER BY key, so ties on the business ordering column resolve
        deterministically: two runs over identical lake data always keep
        the same row.
     2. Downstream change-detection: the bridge copies it into
        nhdw_landing, where it is available as a cheap whole-row delta
        signal.

   Usage in a stg_ model (computed before the dedupe, over every projected
   source column):
       hashed AS (
           SELECT *, {{ source_record_hash(['col_a', 'col_b', ...]) }} AS source_record_hash
           FROM src
       )

   Implementation: cityHash64 of the pipe-joined string concat. cityHash64 is
   non-cryptographic but very fast and stable in CH; collisions at typical
   warehouse scale are negligible. A hash portable across engines would use
   MD5 + lower(hex(...)).
#}
{% macro source_record_hash(columns) -%}
    cityHash64(concat(
        {% for col in columns -%}
            COALESCE(toString({{ col }}), ''){% if not loop.last %},'|',{% endif %}
        {%- endfor %}
    ))
{%- endmacro %}
