# Stock upstream Keycloak with the custom "nhdw" login theme baked in.
# `start-dev` reads themes live from /opt/keycloak/themes, so no `kc.sh build`
# step is needed. Theming rather than forking is the supported, upgrade-safe way
# to brand the hosted login page while keeping PKCE, SSO, social login, MFA and
# lockout intact.
FROM quay.io/keycloak/keycloak:26.0.7

COPY themes/nhdw /opt/keycloak/themes/nhdw
