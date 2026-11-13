# Third-party material

This repository includes or packages the following third-party material. Upstream software pulled as container images or package dependencies is governed by its own licence and is not listed individually.

| Material | Location | Source and terms |
|---|---|---|
| Vector basemaps (Ethiopia, Zambia) | `6_access-points/dashboards/internal-bi/static/ct/*.pmtiles` | Protomaps basemap schema built from OpenStreetMap data. © OpenStreetMap contributors, Open Database License (ODbL). |
| Terrain tiles | `6_access-points/dashboards/internal-bi/static/ct/terrain/` | Terrarium-encoded elevation tiles (Mapzen / AWS Terrain Tiles format). Upstream elevation sources require attribution; record the specific source before redistribution. |
| Map fonts (Noto Sans) | `6_access-points/dashboards/internal-bi/static/ct/fonts/` | Google Noto fonts, SIL Open Font License 1.1. |
| UI fonts | dashboard `ui/` packages via `@fontsource` | Each font's own licence (SIL OFL 1.1 for the families used). |
| World land outline | `*/world-land-110m.geojson` | Natural Earth 1:110m, public domain. |
| Zambia and Ghana ADM1 boundaries | `bundle/profiles/zm-v1/geo/`, `bundle/profiles/gh-v1/geo/` | geoBoundaries, CC BY 4.0. |
| Ethiopia region and woreda boundaries | `bundle/profiles/et-v3/geo/`, dashboard `static/` | Provenance not recorded in the repository; confirm source and terms before redistribution. |
| Zambian facility registry (eLMIS) | `bundle/seeds/entity-model/sample-data/facilities_zm_elmis.json` | Facility-level reference data (code, name, type, district, coordinates) from Zambian eLMIS. Redistribution terms belong to the data owner. |
| Ethiopian facility sample | `bundle/seeds/entity-model/_build/facilities.json` | Sample of the public Ethiopian Master Facility Registry facility list, with generated additions. |
| ICD-10 and ATC code supplements | `bundle/seeds/ref-vocab/supplements/` | Code lists maintained by the World Health Organization; used for reference mapping under WHO's terms. |
| DHIS2 schema diagram | `docs/diagrams/dhis2/dhis2-erd.svg` | DHIS2 is open source (BSD-3-Clause). |
| Cilium Helm chart | `bundle/charts/cilium-1.16.5.tgz` | Cilium, Apache License 2.0. |
| Background image | dashboard `static/assets/bg-mountains.jpg` | Provenance not recorded; confirm before redistribution. |
| Organisation marks | Ethiopian Ministry of Health emblem (`logo-fmoh.png`), Palladium logo | Trademarks of their owners, used to brand the reference deployment. Not licensed for reuse. |
