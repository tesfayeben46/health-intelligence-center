# Country profiles

This document describes how much of the platform follows the active country profile and what is still specific to one country.

## Mechanism

A profile lives in `bundle/profiles/<key>/` and has three layers:

| Layer | Contents | Applied by |
|---|---|---|
| presentation | copy decks, geography, branding, catalogue changes | the gateway, at request time |
| appsec | people, roles, scopes, policy | the security seed step |
| data | which source replicas feed the pipeline | a full pipeline run |

The gateway serves the presentation layer:

    GET /api/profile                  the manifest
    GET /api/profile/copy/{surface}   a resolved copy deck
    GET /api/profile/geo/{name}       boundaries (?profile= for a named profile)
    GET /api/profile/reference        regions, programmes, diseases, commodities (?profile=)
    GET /api/profile/asset/{name}     branding assets
    GET /api/profile/catalog          widgets and pages the profile turns off

`?profile=` lets a page whose data belongs to one country draw that country's geometry regardless of the active profile; the Supply Control Tower page uses it for Zambia. Only installed profiles are served.

`/catalog` is read by the visibility resolver (`permissions.py::_profile_disabled()`) and can only remove visibility. A widget that suits some countries is granted once in the catalogue and disabled by the profiles it does not suit.

## Surfaces that follow the profile

| Surface | Profile-driven |
|---|---|
| public | copy, masthead, hero, footer, globe (`hooks/useProfile.ts`) |
| internal-bi | reference tables, geography, scope label, crest and word mark |
| workshop | crest, word mark, footer owner line, scope label |
| admin | crest, word mark, access-review area picker, decision e-mail sign-off |

## Country-specific parts

- **Control Tower basemap.** `ct/components/MapView.tsx` loads `ethiopia.pmtiles` and centres on Ethiopia. The widget family shows Ethiopian EPSS movements and is disabled in the Zambian profile. Supporting other countries needs a basemap per country.
- **Zambian security layer.** `zm-v1/profile.yaml` points to `seed-pack/app/security/users.zm.yaml` (Zambian users with provincial scopes). It is applied by the security seed step.
- **Data sources.** Six of the seven sources are Ethiopian; eLMIS is Zambian. A profile switch changes presentation; the warehouse contents change only with a pipeline run. The demo-control route applies the presentation layer in-process and reports the other two layers as not applied.

## Sources by country

Several sources are specific national systems rather than generic categories:

| Source | Zambia |
|---|---|
| eLMIS (OpenLMIS) | Yes, the Zambian data layer |
| HCMIS WDM | No, Ethiopian national warehouse system |
| mBrana | No, Ethiopian mobile reporting product |
| MFR | No, Ethiopian master facility registry |
| EthioEMR | No, Ethiopian EMR |
| DHIS2 | Global platform; this instance is Ethiopian |
| Climate / EWARS | Generic, built on Ethiopian geography |

A source with no counterpart in a country is not generated for that country. The surfaces that depend on it are disabled in the profile's `catalog.yaml`.

### Deciding what to disable

Trace each widget to its marts and their vault sources. Marts name their sources in the vault tables they read (`hub_elmis_*`, `sat_hcmis_*`), and `fetchers.py` binds each widget to its marts. A widget whose marts all read an absent source has nothing to show.

Disable at the coarsest grain that applies. `internal-bi/resource` is disabled for `zm-v1` as a page rather than as eleven widgets, because every section reads HCMIS; page-tab elision already drops a page with no visible sections.

Each entry in `catalog.yaml` has a `why` field explaining the reason.

## New surfaces

Any string that names a country, region, administrative level, ministry or fiscal calendar comes from the profile. Fallback text must be country-neutral, so a deployment for one country never shows another country's name when the gateway is unavailable.
