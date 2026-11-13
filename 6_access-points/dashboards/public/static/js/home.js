function publicApp() {
  return {
    // Page nav
    active: 'home',                       // home | data | articles | article
    setPage(p) { this.active = p; window.scrollTo({ top: 0, behavior: 'smooth' }); this.$nextTick(() => this.renderActive()); },
    renderActive() {
      // Charts must be rendered after their containers become visible
      // (ECharts cannot lay out a 0x0 element). We re-init on page change.
      if (this.active === 'home') {
        this.regionChart();
        this.trendChart();
      }
    },

    // Articles (news) state · used by home preview, list page, detail
    articles: [
      { id:'a-001', date:'2026-06-14', category:'PHEM', author:'EPHI Surveillance', tags:['cholera','outbreak','Somali'],
        title:'Cholera outbreak response: Somali Region update (Week 24)',
        summary:'Rapid response teams have been deployed to four woredas in Somali Region. Cumulative case count 247, with 38 new cases in the last 7 days. Vaccination and WASH interventions are ongoing.',
        body:'<p>Following confirmation of cholera cases in <strong>Doolo and Korahe zones</strong>, the Public Health Emergency Management (PHEM) directorate has mobilised four Rapid Response Teams in coordination with the Regional Health Bureau.</p>\
              <p>As of 14 June, the cumulative case count stands at <strong>247</strong>, with 38 cases reported in the last seven days. The case-fatality rate remains under 1.5%. Oral cholera vaccine (OCV) campaigns have launched in three woredas; WASH interventions including chlorinated water points are operational in 21 sites.</p>\
              <p>EPHI continues to coordinate with partners including UNICEF, WHO, and MSF for surge support. The next situation report will publish on 21 June (Week 25).</p>' },
      { id:'a-002', date:'2026-06-11', category:'Press', author:'FMoH Communications', tags:['HSTP-III','policy','consultation'],
        title:'FMoH launches HSTP-III consultations',
        summary:'The Federal Ministry of Health begins national consultations for the Health Sector Transformation Plan III (2025/26 to 2029/30), focusing on quality, equity, and digital health.',
        body:'<p>His Excellency the Minister of Health officially launched the consultation process for the next five-year transformation plan today at the Skylight Hotel in Addis Ababa.</p>\
              <p>HSTP-III builds on the lessons of HSTP-II and orients the sector around three pillars: <strong>quality of care</strong>, <strong>equity in coverage</strong>, and <strong>digital transformation</strong>. Regional consultations will run through August 2026.</p>\
              <p>Civil society, professional associations, partners, and citizens are invited to submit input through the FMoH portal. The full draft will be published for public review in November.</p>' },
      { id:'a-003', date:'2026-06-08', category:'Advisory', author:'EPI Directorate', tags:['immunisation','Penta3','pastoralist'],
        title:'Routine immunization campaign: June 16 to 22',
        summary:'The national catch-up immunization campaign targets children under 5 in pastoralist regions. Caregivers are encouraged to visit their nearest health post.',
        body:'<p>The Expanded Programme on Immunization (EPI) is conducting a one-week catch-up campaign across Afar, Somali, Benishangul-Gumuz, and Gambella regions from <strong>16 to 22 June 2026</strong>.</p>\
              <p>Antigens included: BCG, Penta1-3, MCV1, MCV2, IPV, PCV, rotavirus. The campaign uses outreach teams covering 412 sites including health posts, mobile clinics, and school grounds.</p>\
              <p>Caregivers are encouraged to bring their child\'s immunization card. Lost cards can be reissued at the site.</p>' },
      { id:'a-004', date:'2026-06-03', category:'PHEM', author:'EPHI Surveillance', tags:['measles','Tigray','outbreak'],
        title:'Measles outbreak in Tigray Region declared contained',
        summary:'After 12 weeks of response, the measles outbreak in Tigray has been declared contained. Total cases 1,847 with 11 deaths; vaccination coverage post-response now exceeds 92% across affected woredas.',
        body:'<p>The PHEM weekly bulletin confirms that the measles outbreak that began in central Tigray in March has been declared contained as of Week 22.</p>\
              <p>The outbreak affected 18 woredas with <strong>1,847 confirmed cases</strong> and 11 deaths (CFR 0.6%). The Rapid Response Team conducted three reactive vaccination rounds reaching 280,000 children.</p>\
              <p>Routine surveillance continues; the next risk assessment is scheduled for Week 26.</p>' },
      { id:'a-005', date:'2026-05-28', category:'Press', author:'FMoH Director-General', tags:['HEARTS','NCD','HTN'],
        title:'HEARTS programme expands to 142 additional health centres',
        summary:'The WHO HEARTS technical package for hypertension management is being rolled out to 142 more primary health centres, bringing total coverage to 511 facilities across 11 regions.',
        body:'<p>The Federal Ministry of Health announced today the expansion of the HEARTS programme (a WHO-led standardised approach to managing hypertension in primary care) to <strong>142 additional health centres</strong>.</p>\
              <p>The expansion targets areas where HTN screening coverage has been below 30%. Each facility receives a kit including digital BP monitors, validated training, simplified treatment protocols, and a 6-month medication starter package.</p>\
              <p>This brings total HEARTS coverage to 511 health centres across 11 regions, on track for the HSTP-II target of 600 centres by end of 2026.</p>' },
      { id:'a-006', date:'2026-05-22', category:'Advisory', author:'EFDA', tags:['cold chain','safety','EPI'],
        title:'Cold-chain integrity advisory: BCG lot 2024-LOT-39812',
        summary:'EFDA issued an advisory regarding cold-chain temperature excursions affecting seven facilities. BCG vaccine lot 2024-LOT-39812 has been quarantined pending re-test.',
        body:'<p>The Ethiopian Food and Drug Authority (EFDA), in coordination with EPI, has issued a cold-chain integrity advisory for BCG vaccine lot <strong>2024-LOT-39812</strong>.</p>\
              <p>Temperature monitoring at seven facilities recorded excursions above 8°C for periods longer than 4 hours. Affected doses (estimated 2,118) have been quarantined and will be re-tested for potency.</p>\
              <p>Facilities should follow the standard quarantine protocol and report through the AEFI surveillance system. New stock from a different lot will be redistributed within 5 days.</p>' },
      { id:'a-007', date:'2026-05-14', category:'Research', author:'Public Health Institute', tags:['EWARS+','climate','forecasting'],
        title:'EWARS+ climate-driven forecasting model goes live',
        summary:'EPHI announces the operational deployment of EWARS+, a climate-driven outbreak forecasting model providing weekly woreda-level alarm probabilities for cholera, malaria, and dengue.',
        body:'<p>The Public Health Institute, in partnership with EPHI, has formally deployed <strong>EWARS+</strong>: an extension of the WHO EWARS framework that integrates rainfall, temperature, and humidity data with surveillance reporting.</p>\
              <p>The model produces weekly alarm probabilities at woreda level for cholera, malaria, dengue, and meningitis. Probabilities are published every Monday and feed directly into PHEM\'s weekly bulletin.</p>\
              <p>Initial validation shows a lead time of 2-3 weeks ahead of outbreak confirmation, with sensitivity 78% and specificity 84% across the 2024-2025 retrospective dataset.</p>' },
      { id:'a-008', date:'2026-05-06', category:'Programme', author:'MNCH Directorate', tags:['MNCH','ANC4','PNC1'],
        title:'MNCH continuum of care quality improvement collaborative',
        summary:'A new QI collaborative is launching in 12 regions targeting the ANC1, ANC4, SBA and PNC1 continuum. Funded by the global Health Quality Initiative.',
        body:'<p>The MNCH Directorate is launching a quality-improvement collaborative across <strong>12 regions and 280 health centres</strong> to address gaps in the continuum of maternal care.</p>\
              <p>Cohort tracking will follow women from ANC1 through PNC1, with monthly facility-level review meetings. The intervention package includes training, mentoring visits, supply support, and digital tracking via DHIS2.</p>\
              <p>The target outcome: increase ANC4 completion from 62% to 75%, and PNC1-within-48h from 48% to 65% by end of 2026.</p>' },
      { id:'a-009', date:'2026-04-29', category:'Press', author:'FMoH State Minister', tags:['donors','PEPFAR','HIV'],
        title:'Ethiopia signs renewed HIV programme cooperation with PEPFAR',
        summary:'The Federal Ministry of Health signed a renewed five-year cooperation agreement with PEPFAR, focused on the 95-95-95 targets and PMTCT.',
        body:'<p>Ethiopia\'s renewed cooperation with PEPFAR (President\'s Emergency Plan for AIDS Relief) covers the period 2026-2030, with a programmatic focus on reaching the 95-95-95 targets, eliminating mother-to-child transmission, and integrating HIV services into primary care.</p>\
              <p>The agreement carries technical and financial support for ART scale-up, supply-chain strengthening, and the second-line ART transition.</p>\
              <p>Current cascade indicators: 89% awareness, 78% on ART, 89% virally suppressed.</p>' },
      { id:'a-010', date:'2026-04-21', category:'Advisory', author:'PHEM', tags:['rainy season','flooding','preparedness'],
        title:'Pre-rainy-season preparedness advisory',
        summary:'PHEM has issued the annual pre-rainy-season preparedness advisory covering cholera, malaria, dengue, and post-flood public health risks for Belg (March-May) and Kiremt (June-September) seasons.',
        body:'<p>The Public Health Emergency Management directorate has issued its annual pre-rainy-season preparedness advisory ahead of the <strong>Kiremt season (June-September)</strong>.</p>\
              <p>Anticipated risks: water-borne outbreaks (cholera, AWD), vector-borne diseases (malaria, dengue), and post-flood public-health hazards in lowland regions.</p>\
              <p>Regional Health Bureaus are advised to verify pre-positioned stock of ORS, RDTs, ITNs, and IRS supplies, and to confirm functional incident command structures by 1 June.</p>' },
    ],
    articleQuery: '',
    articleCategory: 'all',
    articleSort: 'newest',
    articlePage: 0,
    articlePageSize: 5,
    currentArticle: null,

    filteredArticles() {
      const q = this.articleQuery.trim().toLowerCase();
      return this.articles
        .filter(a => this.articleCategory === 'all' || a.category === this.articleCategory)
        .filter(a => !q || a.title.toLowerCase().includes(q) || a.summary.toLowerCase().includes(q) || a.body.toLowerCase().includes(q) || a.category.toLowerCase().includes(q) || a.tags.some(t => t.toLowerCase().includes(q)))
        .sort((a, b) => {
          if (this.articleSort === 'newest') return b.date.localeCompare(a.date);
          if (this.articleSort === 'oldest') return a.date.localeCompare(b.date);
          if (this.articleSort === 'az')     return a.title.localeCompare(b.title);
          return 0;
        });
    },
    pagedArticles() {
      const all = this.filteredArticles();
      return all.slice(this.articlePage * this.articlePageSize, (this.articlePage + 1) * this.articlePageSize);
    },
    totalArticlePages() { return Math.max(1, Math.ceil(this.filteredArticles().length / this.articlePageSize)); },
    openArticle(id) {
      this.currentArticle = this.articles.find(a => a.id === id) || null;
      if (this.currentArticle) this.setPage('article');
    },

    // Catalogue (Open data page)
    catalogQuery: '',
    catalogTier: 'all',         // all | aggregate | facility-level | bulletin
    catalogTopic: 'all',
    catalogSort: 'updated',
    catalogPage: 0,
    catalogPageSize: 6,
    datasets: [
      { id:'D-001', title:'National coverage indicators by region', topic:'EPI · MNCH · HIV · TB', tier:'aggregate', period:'2024-Q1 to 2026-Q2', formats:['CSV','JSON','XLSX'], sizeMb:4.2, updated:'2026-06-21', downloads:1247, doi:'10.x/nhdw/D-001', desc:'14 regions by 8 flagship indicators by quarterly. The data behind the HSTP-II midterm review.' },
      { id:'D-002', title:'Facility distribution by region by tier', topic:'Health Facility Registry', tier:'aggregate', period:'2026-Q2', formats:['CSV','GeoJSON'], sizeMb:1.8, updated:'2026-06-20', downloads:892, doi:'10.x/nhdw/D-002', desc:'Counts of health posts, health centres, primary and general hospitals per region. Includes coordinates.' },
      { id:'D-003', title:'PHEM weekly IDSR bulletins', topic:'Surveillance', tier:'bulletin', period:'2023-W01 to 2026-W24', formats:['PDF','JSON'], sizeMb:76, updated:'2026-06-19', downloads:3104, doi:'10.x/nhdw/D-003', desc:'Weekly notifiable-disease bulletins published by PHEM. PDF for reading, JSON for analytics.' },
      { id:'D-004', title:'Vaccination programme metrics', topic:'EPI', tier:'aggregate', period:'2024 to 2026', formats:['CSV','XLSX'], sizeMb:12, updated:'2026-06-18', downloads:680, doi:'10.x/nhdw/D-004', desc:'BCG, Penta1-3, MCV1, MCV2, IPV, PCV, rotavirus, HPV by region and month.' },
      { id:'D-005', title:'NCD prevalence: STEPS 2023', topic:'NCD', tier:'aggregate', period:'2023', formats:['XLSX','PDF'], sizeMb:3.4, updated:'2026-04-12', downloads:512, doi:'10.x/nhdw/D-005', desc:'WHO STEPS survey of NCD risk factors. Hypertension, diabetes, tobacco, alcohol, physical activity.' },
      { id:'D-006', title:'Maternal mortality ratio · 2019-2025', topic:'MNCH', tier:'aggregate', period:'2019 to 2025', formats:['CSV','XLSX'], sizeMb:0.9, updated:'2026-03-15', downloads:778, doi:'10.x/nhdw/D-006', desc:'MMR per 100k live births, by region. Cross-sectional, vital registration + facility delivery audit.' },
      { id:'D-007', title:'Cold-chain incident log', topic:'Supply chain', tier:'aggregate', period:'2024 to 2026', formats:['CSV','JSON'], sizeMb:2.1, updated:'2026-06-15', downloads:233, doi:'10.x/nhdw/D-007', desc:'Cold-chain excursions logged by facility (de-identified). Useful for QI research and OR.' },
      { id:'D-008', title:'EWARS+ woreda alarm-probability series', topic:'Surveillance', tier:'aggregate', period:'2022-W01 to 2026-W24', formats:['CSV','Parquet'], sizeMb:48, updated:'2026-06-22', downloads:418, doi:'10.x/nhdw/D-008', desc:'Climate-adjusted weekly alarm probability for 1148 woredas. Curated by EPHI for forecasting research.' },
      { id:'D-009', title:'TB cohort outcomes: 2024 register', topic:'TB', tier:'aggregate', period:'2024', formats:['CSV','XLSX'], sizeMb:1.3, updated:'2026-02-28', downloads:357, doi:'10.x/nhdw/D-009', desc:'Treatment success / failure / lost-to-follow-up per region, MDR-TB included.' },
      { id:'D-010', title:'Health workforce density', topic:'Workforce', tier:'aggregate', period:'2024 to 2026', formats:['CSV','XLSX'], sizeMb:0.6, updated:'2026-05-30', downloads:289, doi:'10.x/nhdw/D-010', desc:'Doctors, nurses, midwives, pharmacists per 10k population by region.' },
      { id:'D-011', title:'HIV cascade: annual', topic:'HIV', tier:'aggregate', period:'2020 to 2025', formats:['CSV','XLSX'], sizeMb:1.1, updated:'2026-05-22', downloads:842, doi:'10.x/nhdw/D-011', desc:'90-90-90 / 95-95-95 cascade per region. ART coverage, retention, viral suppression.' },
      { id:'D-012', title:'Population & demographics (CSA cuts)', topic:'Reference', tier:'aggregate', period:'2024 estimate', formats:['CSV','GeoJSON'], sizeMb:1.6, updated:'2026-01-15', downloads:1582, doi:'10.x/nhdw/D-012', desc:'CSA mid-year population estimates by woreda, with age & sex breakdowns. Denominator for every coverage indicator.' },
    ],
    topics: ['EPI', 'MNCH', 'HIV', 'TB', 'NCD', 'Surveillance', 'Supply chain', 'Workforce', 'Reference', 'Health Facility Registry'],
    matches() {
      const q = this.catalogQuery.trim().toLowerCase();
      const t = this.catalogTopic;
      return this.datasets
        .filter(d => this.catalogTier === 'all' || d.tier === this.catalogTier)
        .filter(d => t === 'all' || d.topic === t || d.topic.split('·').map(s=>s.trim()).includes(t))
        .filter(d => !q || d.title.toLowerCase().includes(q) || d.desc.toLowerCase().includes(q) || d.topic.toLowerCase().includes(q))
        .sort((a,b) => {
          if (this.catalogSort === 'updated')   return b.updated.localeCompare(a.updated);
          if (this.catalogSort === 'downloads') return b.downloads - a.downloads;
          if (this.catalogSort === 'title')     return a.title.localeCompare(b.title);
          return 0;
        });
    },
    paged() {
      const all = this.matches();
      return all.slice(this.catalogPage * this.catalogPageSize, (this.catalogPage + 1) * this.catalogPageSize);
    },
    totalPages() { return Math.max(1, Math.ceil(this.matches().length / this.catalogPageSize)); },

    // Insights page extras
    insightsScorecards() { /* extension hook · today's static KPI strip is enough */ },

    modal: null,
    authMethod: 'password',
    selectedDashboard: '',
    dashboardLabels: {
      'internal-bi': 'Internal BI',
      'infra-admin': 'Infra Admin',
      'community-workshop': 'Workshop Portal',
      'submission-portal': 'Submission Portal',
      'dbt-docs': 'dbt Docs',
    },
    dashboardDescriptions: {
      'internal-bi': 'Programme analytics + cascade + supply + equity. Roles: FMoH staff, RHB, programme directors.',
      'infra-admin': 'Container health, user/role admin, audit logs, SSO config. Roles: IT operations, security officers.',
      'community-workshop': 'Open workshops, datasets browser, community forum. Open with registration.',
      'submission-portal': 'Standard data submissions and validation. Roles: facility data clerks, programme M&E.',
      'dbt-docs': 'Auto-generated lineage and model docs. Public.',
    },

    // Access request form state
    accessForm: { name:'', email:'', phone:'', org:'', role:'', dashboards: [], purpose: '' },
    submittedRequest: null,

    submitAccessRequest() {
      // Placeholder: sets a fixed tracking ID and does not call the
      // access-requests API (the React app's pub-access-modal does).
      this.submittedRequest = 'AR-2026-0247';
    },

    resetAccessForm() {
      this.submittedRequest = null;
      this.modal = null;
      this.accessForm = { name:'', email:'', phone:'', org:'', role:'', dashboards: [], purpose: '' };
    },

    initCharts() {
      this.$nextTick(() => {
        this.heroGlobe();
        this.regionChart();
        this.trendChart();
      });
    },

    // Dot-globe · canvas-rendered Earth where dots fall on actual
    // continent landmass. We load a low-res world land GeoJSON
    // (Natural Earth 110m), do point-in-polygon for each candidate
    // sphere point, and keep only land dots. East Africa (Ethiopia
    // and neighbours) is highlighted in the accent colour so the
    // dot-pattern reads "Earth with the warehouse's home region".
    async heroGlobe() {
      const canvas = document.getElementById('hero-globe'); if (!canvas) return;
      const ctx = canvas.getContext('2d');
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      const resize = () => {
        const r = canvas.getBoundingClientRect();
        canvas.width  = Math.max(1, r.width  * dpr);
        canvas.height = Math.max(1, r.height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      };
      resize();
      window.addEventListener('resize', resize);

      // Load world land polygons (Natural Earth 110m, ~237KB once)
      let landPolys = [];
      try {
        const geo = await (await fetch('/static/assets/world-land-110m.geojson')).json();
        const flat = [];
        for (const f of (geo.features || [])) {
          const g = f.geometry; if (!g) continue;
          if (g.type === 'Polygon')      flat.push(g.coordinates);
          if (g.type === 'MultiPolygon') for (const p of g.coordinates) flat.push(p);
        }
        landPolys = flat.map(rings => {
          const outer = rings[0];
          let minX =  Infinity, maxX = -Infinity, minY =  Infinity, maxY = -Infinity;
          for (const [x, y] of outer) {
            if (x < minX) minX = x; if (x > maxX) maxX = x;
            if (y < minY) minY = y; if (y > maxY) maxY = y;
          }
          return { rings, bbox: [minX, minY, maxX, maxY] };
        });
      } catch (e) {
        console.warn('world land mask not loaded; falling back to evenly-spaced sphere', e);
      }

      // Ray-cast point-in-polygon · handles outer ring + holes
      const pointInRing = (x, y, ring) => {
        let inside = false;
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const xi = ring[i][0], yi = ring[i][1];
          const xj = ring[j][0], yj = ring[j][1];
          const intersect = ((yi > y) !== (yj > y))
            && (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi);
          if (intersect) inside = !inside;
        }
        return inside;
      };
      const isLand = (lng, lat) => {
        if (!landPolys.length) return true;     // fallback: treat all as land
        for (let i = 0; i < landPolys.length; i++) {
          const p = landPolys[i];
          if (lng < p.bbox[0] || lng > p.bbox[2] || lat < p.bbox[1] || lat > p.bbox[3]) continue;
          if (!pointInRing(lng, lat, p.rings[0])) continue;
          let inHole = false;
          for (let h = 1; h < p.rings.length; h++) {
            if (pointInRing(lng, lat, p.rings[h])) { inHole = true; break; }
          }
          if (!inHole) return true;
        }
        return false;
      };

      // Generate sphere candidates · Fibonacci lattice, keep only land
      const N = 7200;
      const PHI = Math.PI * (Math.sqrt(5) - 1);
      const points = [];
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / (N - 1)) * 2;
        const r = Math.sqrt(1 - y * y);
        const t = PHI * i;
        const x = Math.cos(t) * r;
        const z = Math.sin(t) * r;
        const lat = Math.asin(y) * 180 / Math.PI;
        const lng = Math.atan2(x, z) * 180 / Math.PI;     /* fix mirror: lng=0 at +z (front), east is +x (right) */
        if (!isLand(lng, lat)) continue;
        points.push({ x, y, z });
      }

      // Single-point Ethiopia marker · projected to the sphere, drawn as
      // a glowing star ON TOP of the dots when it's on the front-facing
      // half. Approximate centroid: lat 9.5° N, lng 40° E.
      const ETH_LAT = 9.5, ETH_LNG = 40.0;
      const latR = ETH_LAT * Math.PI / 180, lngR = ETH_LNG * Math.PI / 180;
      // Standard convention to match the lattice's new labelling:
      //   x = cos(lat)*sin(lng): east positive
      //   z = cos(lat)*cos(lng): prime meridian at +z (front)
      const star = {
        x: Math.cos(latR) * Math.sin(lngR),
        y: Math.sin(latR),
        z: Math.cos(latR) * Math.cos(lngR),
      };

      // Pick up the accent colour from CSS · stays in sync with --accent
      const accentHex = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#0374b8').trim();
      const accentRGB = (() => {
        const m = accentHex.match(/#([0-9a-f]{6})/i);
        if (!m) return [3, 116, 184];
        const v = parseInt(m[1], 16);
        return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
      })();

      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      // Start with Ethiopia (~40° E lng) at the front · positive rotY in
      // the new convention rotates west-to-east, matching Earth.
      let rotY = 40 * Math.PI / 180;
      // Tilt the globe forward so the silhouette feels 3D, not a flat disc
      const TILT = -16 * Math.PI / 180;
      const cosT = Math.cos(TILT), sinT = Math.sin(TILT);

      const draw = (now) => {
        const rect = canvas.getBoundingClientRect();
        const W = rect.width, H = rect.height;
        ctx.clearRect(0, 0, W, H);
        const cx = W / 2, cy = H / 2;
        const radius = Math.min(W, H) * 0.46;
        const cosY = Math.cos(rotY), sinY = Math.sin(rotY);

        // Sphere body left transparent: no dark disc. The cream hero
        // shows through · particles need to carry the visibility alone.

        // Continent particles · saturated deep teal · distinct from
        // navy chrome and from the FMoH-blue Ethiopia star, not flag
        // colours (no green/yellow/red/gold). Renders as a luminous
        // jewel-tone constellation against the cream background.
        for (let i = 0; i < points.length; i++) {
          const p = points[i];
          const rx = p.x * cosY - p.z * sinY;
          const ry = p.y;
          const rz = p.x * sinY + p.z * cosY;
          const ty = ry * cosT - rz * sinT;
          const tz = ry * sinT + rz * cosT;
          if (tz < -0.02) continue;            // back of sphere

          const screenX = cx + rx * radius;
          const screenY = cy - ty * radius;
          const depth = (tz + 1) / 2;          // 0 = back, 1 = front
          const r = 1.15 + depth * 0.95;
          const a = 0.70 + depth * 0.30;
          ctx.fillStyle = `rgba(${accentRGB[0]}, ${accentRGB[1]}, ${accentRGB[2]}, ${a})`;  // same FMoH blue as the header accent
          ctx.beginPath();
          ctx.arc(screenX, screenY, r, 0, Math.PI * 2);
          ctx.fill();
        }

        // Ethiopia marker · single pulsing star drawn on top
        const srx = star.x * cosY - star.z * sinY;
        const sry = star.y;
        const srz = star.x * sinY + star.z * cosY;
        const sty = sry * cosT - srz * sinT;
        const stz = sry * sinT + srz * cosT;
        if (stz > 0.06) {
          const sx = cx + srx * radius;
          const sy = cy - sty * radius;
          const depth = (stz + 1) / 2;
          const t = (now || performance.now()) / 1000;
          const pulse = 0.85 + 0.15 * Math.sin(t * 2.0);    // soft 2π/1.0s pulse

          // Soft outer halo
          const haloR = (20 + depth * 8) * pulse;
          const halo = ctx.createRadialGradient(sx, sy, 0, sx, sy, haloR);
          halo.addColorStop(0,   `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},${0.45 * pulse})`);
          halo.addColorStop(0.4, `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},${0.12 * pulse})`);
          halo.addColorStop(1,   `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},0)`);
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(sx, sy, haloR, 0, Math.PI * 2);
          ctx.fill();

          // Cobalt ring
          ctx.fillStyle = `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},${0.92})`;
          ctx.beginPath();
          ctx.arc(sx, sy, 3.5 + depth * 0.8, 0, Math.PI * 2);
          ctx.fill();

          // Bright white core
          ctx.fillStyle = `rgba(255,255,255,${0.92 * pulse})`;
          ctx.beginPath();
          ctx.arc(sx, sy, 1.5 + depth * 0.4, 0, Math.PI * 2);
          ctx.fill();
        }

        if (!reduced) rotY += 0.0014;          // ~75-sec revolution
        requestAnimationFrame(draw);
      };
      draw();
    },

    regionChart() {
      const c = echarts.init(document.getElementById('region-chart'));
      const regions = ['Addis Ababa','Sidama','Harari','Dire Dawa','Amhara','Oromia','South Ethiopia','Central','S.West','Tigray','Benishangul','Gambella','Afar','Somali'];
      const penta = [96,93,94,92,89,90,86,84,81,75,68,62,54,48];
      const anc4  = [78,71,73,70,65,64,60,57,53,50,45,38,32,27];
      const sba   = [92,76,77,72,58,56,52,48,44,42,35,30,24,21];
      c.setOption({
        grid: {left: 70, right: 12, top: 30, bottom: 40},
        legend: {data: ['Penta3','ANC4','SBA'], top: 0, textStyle:{fontSize: 11, color:'#475569'}},
        tooltip: {trigger:'axis', axisPointer:{type:'shadow'}},
        xAxis: {type:'value', max:100, axisLabel:{formatter:'{value}%', fontSize:10, color:'#94a3b8'}, splitLine:{lineStyle:{color:'#e2e6ec'}}},
        yAxis: {type:'category', data: regions, axisTick:{show:false}, axisLine:{show:false}, axisLabel:{fontSize:11, color:'#475569'}},
        series: [
          {name:'Penta3', type:'bar', data: penta, itemStyle:{color:'#0d7d5a'}, barWidth:8},
          {name:'ANC4',   type:'bar', data: anc4,  itemStyle:{color:'#c47c2c'}, barWidth:8},
          {name:'SBA',    type:'bar', data: sba,   itemStyle:{color:'#b6293a'}, barWidth:8},
        ],
      });
    },

    trendChart() {
      const c = echarts.init(document.getElementById('trend-chart'));
      const months = ['Jun 24','Jul','Aug','Sep','Oct','Nov','Dec','Jan 25','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Jan 26','Feb','Mar','Apr','May','Jun'];
      const fn = (b, amp) => months.map((_,i) => b + amp * Math.cos(2*Math.PI*(i-3)/12) + (Math.random()-0.5)*1.5);
      c.setOption({
        grid: {left: 40, right: 12, top: 30, bottom: 32},
        legend: {data:['Penta3','ANC4','SBA','mCPR'], top: 0, textStyle:{fontSize:11, color:'#475569'}},
        tooltip: {trigger:'axis'},
        xAxis: {type:'category', data: months, axisLabel:{fontSize:9, color:'#94a3b8'}, axisLine:{lineStyle:{color:'#cbd2dc'}}},
        yAxis: {type:'value', min:0, max:100, axisLabel:{formatter:'{value}%', fontSize:10}, splitLine:{lineStyle:{color:'#e2e6ec'}}},
        series: [
          {name:'Penta3', type:'line', smooth:true, symbol:'none', data: fn(89, 3), lineStyle:{color:'#0d7d5a',width:2.2}},
          {name:'ANC4',   type:'line', smooth:true, symbol:'none', data: fn(60, 3), lineStyle:{color:'#c47c2c',width:2.2}},
          {name:'SBA',    type:'line', smooth:true, symbol:'none', data: fn(52, 3), lineStyle:{color:'#b6293a',width:2.2}},
          {name:'mCPR',   type:'line', smooth:true, symbol:'none', data: fn(40, 2), lineStyle:{color:'#2c6dca',width:2.2}},
        ],
      });
    },
  };
}
