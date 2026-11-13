# Fayda replica

A small FastAPI service that models Ethiopia's Fayda national-ID provider for the platform's identity-verification step. It runs outside the cluster, like the source replicas, because in production Fayda is a third-party system. All identities are synthetic.

## Layout

```
app/main.py          the service: OIDC endpoints and the e-mail OTP verification flow
seed/citizens.yaml   36 synthetic citizens (FAN, name, birthdate, phone, example.org e-mail)
Dockerfile           python:3.12-slim image (nhdw/fayda-replica:1.0.0)
docker-compose.yml   container fayda-replica, published on 8096 (app listens on 8000)
.env.template        SMTP settings for the OTP mailer (placeholders)
```

## How it is used

The public dashboard's access-request form asks a visitor to prove they hold a Fayda identity. The dashboard proxies two calls to this service:

| Endpoint | Behaviour |
|---|---|
| `POST /verify/challenge` `{fan, email}` | Checks that the FAN exists and the supplied e-mail matches the one on record, stores a 6-digit OTP (10-minute TTL, 30-second resend cooldown, 5 attempts) and e-mails it through Fayda's own SMTP. Returns the masked address. |
| `POST /verify/confirm` `{fan, code}` | Validates the code (single use) and returns the identity for the form to pre-fill. |

The OAuth 2.0 / OpenID Connect endpoints (`/.well-known/openid-configuration`, `/jwks`, `/authorize`, `/token`, `/userinfo`) are also implemented, with an RSA signing key generated at each start. The dashboard flow does not use them.

## Usage

```bash
cp 0_external/fayda/.env.template 0_external/fayda/.env    # optional: SMTP for real delivery
docker compose -f 0_external/fayda/docker-compose.yml up -d --build
curl http://localhost:8096/.well-known/openid-configuration
```

`bundle/scripts/sources-up.sh up` starts it with the other external stacks. The dashboard pod reaches it at `http://host.k3d.internal:8096`; a browser at `http://localhost:8096`.

## Configuration

| Variable | Purpose |
|---|---|
| `FAYDA_ISSUER` | Issuer URL in tokens and discovery (compose sets `http://localhost:8096`) |
| `FAYDA_CLIENT_ID`, `FAYDA_CLIENT_SECRET` | OIDC client credentials (development defaults) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | OTP mailer, separate from the gateway's `smtp-secrets` |

Without SMTP settings the OTP is still generated and written to the container log (`[fayda][DEV-ONLY]`), and no e-mail is sent.

## Notes

- Development and demonstration only. Codes, auth codes and tokens are held in memory and lost on restart.
- Citizen e-mail addresses use the reserved `example.org` domain. To receive OTP mail in a test environment, replace them with mailboxes you control.
- To integrate with real Fayda, point the public dashboard's `FAYDA_*` settings at the production endpoints.
