{#
   Override dbt's default schema-name builder so `+schema: nhdw_vault_raw` in
   dbt_project.yml resolves to the literal schema name (not the default
   `<profile_target>_nhdw_vault_raw` concat), the usual override in Data
   Vault dbt projects.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
