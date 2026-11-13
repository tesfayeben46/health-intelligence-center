{{/*
nhdw.image: full image ref for a component: <repository>/<name>:<tag>.
Usage: {{ include "nhdw.image" (dict "name" "internal-bi" "ctx" .) }}
e.g. nhdw/internal-bi:1.0.0 locally, registry.example.com/nhdw/internal-bi:1.0.0 in prod.
The tag is the immutable image.tag value; repository swaps in the registry for prod.
*/}}
{{- define "nhdw.image" -}}
{{- printf "%s/%s:%s" .ctx.Values.image.repository .name .ctx.Values.image.tag -}}
{{- end -}}

{{/*
nhdw.kcPublicBase: the PUBLIC base URL of Keycloak. Derived from the SINGLE hostBase value
(https://keycloak.<hostBase>) so one knob moves the whole OIDC issuer/hostname; an explicit
keycloak.publicBase value overrides it if ever needed.
*/}}
{{- define "nhdw.kcPublicBase" -}}
{{- .Values.keycloak.publicBase | default (printf "https://keycloak.%s" .Values.hostBase) -}}
{{- end -}}
