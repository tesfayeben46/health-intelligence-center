/**
 * Hero dot-globe: mounts on the supplied canvas and runs its own
 * requestAnimationFrame loop, with no React state.
 *
 * Country-agnostic. The geometry filenames come from the profile, and the
 * marker centroid, bounding box and opening rotation are either supplied by
 * the profile or derived from the downloaded geometry, so they cannot disagree
 * with the shape being drawn. Geometry is fetched from
 * /api/proxy/profile/geo/*, so a country swap needs no change here.
 */
type GlobeOpts = {
  /** bbox of the host country, [minLng, minLat, maxLng, maxLat], from the profile */
  bbox?: [number, number, number, number];
  /** marker + opening rotation; defaults to the centre of the bbox */
  centroid?: [number, number];
  /** geometry filenames within the active profile */
  homeGeo?: string;
  worldGeo?: string;
};

export async function heroGlobe(canvas: HTMLCanvasElement, opts: GlobeOpts = {}): Promise<void> {
  if (!canvas) return;
  const ctx = canvas.getContext("2d")!;
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
        const worldUrl = `/api/proxy/profile/geo/${opts.worldGeo || 'world-land-110m.geojson'}`;
        const geo = await (await fetch(worldUrl)).json();
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

      // Load the host country's polygons (admin-1 union) so it renders
      // as a dense, country-shaped cluster of dots instead of a single
      // dot. The bounding box seeds the dense sampling below; it is computed
      // from the geometry unless the profile already supplied it.
      let homePolys: Array<{ rings: number[][][], bbox: number[] }> = [];
      // Only a last resort: if geometry fails to load and the profile gave no
      // bbox, a whole-globe box means the dense fill simply finds nothing,
      // which degrades to "world dots, no host highlight" instead of drawing a
      // wrong country.
      let homeBBox: number[] = opts.bbox ? [...opts.bbox] : [0, 0, -1, -1];
      try {
        const homeUrl = `/api/proxy/profile/geo/${opts.homeGeo || 'admin1.geojson'}`;
        const eg = await (await fetch(homeUrl)).json();
        const flat: number[][][][] = [];
        for (const f of (eg.features || [])) {
          const g = f.geometry; if (!g) continue;
          if (g.type === 'Polygon')      flat.push(g.coordinates);
          if (g.type === 'MultiPolygon') for (const p of g.coordinates) flat.push(p);
        }
        let mnX = Infinity, mnY = Infinity, mxX = -Infinity, mxY = -Infinity;
        homePolys = flat.map(rings => {
          let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
          for (const [x, y] of rings[0]) {
            if (x < a) a = x; if (x > c) c = x; if (y < b) b = y; if (y > d) d = y;
            if (x < mnX) mnX = x; if (x > mxX) mxX = x; if (y < mnY) mnY = y; if (y > mxY) mxY = y;
          }
          return { rings, bbox: [a, b, c, d] };
        });
        if (isFinite(mnX)) homeBBox = [mnX, mnY, mxX, mxY];
      } catch (e) {
        console.warn('host-country geometry not loaded; globe falls back to a marker only', e);
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

      const isHome = (lng: number, lat: number) => {
        for (let i = 0; i < homePolys.length; i++) {
          const p = homePolys[i];
          if (lng < p.bbox[0] || lng > p.bbox[2] || lat < p.bbox[1] || lat > p.bbox[3]) continue;
          if (pointInRing(lng, lat, p.rings[0])) return true;
        }
        return false;
      };
      // Project a (lng,lat) to the unit-sphere xyz used by the lattice.
      const toXYZ = (lng: number, lat: number) => {
        const la = lat * Math.PI / 180, lo = lng * Math.PI / 180;
        return { x: Math.cos(la) * Math.sin(lo), y: Math.sin(la), z: Math.cos(la) * Math.cos(lo) };
      };

      // Generate sphere candidates · Fibonacci lattice, keep only land
      const N = 7200;
      const PHI = Math.PI * (Math.sqrt(5) - 1);
      const points: Array<{ x: number, y: number, z: number, home: boolean }> = [];
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / (N - 1)) * 2;
        const r = Math.sqrt(1 - y * y);
        const t = PHI * i;
        const x = Math.cos(t) * r;
        const z = Math.sin(t) * r;
        const lat = Math.asin(y) * 180 / Math.PI;
        const lng = Math.atan2(x, z) * 180 / Math.PI;     /* fix mirror: lng=0 at +z (front), east is +x (right) */
        if (!isLand(lng, lat)) continue;
        points.push({ x, y, z, home: isHome(lng, lat) });
      }

      // DENSE host-country fill: a fine grid over its bbox so the host nation
      // reads as a bright, solid, correctly-shaped cluster rather than the
      // sparse handful of global-lattice dots that happen to land inside it.
      // Step scales with the country's size: a fixed 0.40 degrees gives Ethiopia
      // a crisp stipple but would emit ~180k points for a country the size of
      // Canada and stall the page. Target roughly the same dot COUNT instead.
      {
        const [aX, aY, cX, dY] = homeBBox;
        const spanX = cX - aX, spanY = dY - aY;
        if (spanX > 0 && spanY > 0) {
          const TARGET = 1500;                       // ~Ethiopia's count at 0.40
          const STEP = Math.max(0.15, Math.min(2.0, Math.sqrt((spanX * spanY) / TARGET)));
          for (let lng = aX; lng <= cX; lng += STEP) {
            for (let lat = aY; lat <= dY; lat += STEP) {
              if (isHome(lng, lat)) { const q = toXYZ(lng, lat); points.push({ x: q.x, y: q.y, z: q.z, home: true }); }
            }
          }
        }
      }

      // Single-point host marker · projected to the sphere, drawn as a glowing
      // star ON TOP of the dots when it is on the front-facing half. Position
      // comes from the profile, else the centre of the geometry's own bbox.
      const bboxCentre: [number, number] = (homeBBox[2] >= homeBBox[0])
        ? [(homeBBox[0] + homeBBox[2]) / 2, (homeBBox[1] + homeBBox[3]) / 2]
        : [0, 0];
      const [HOME_LNG, HOME_LAT] = opts.centroid ?? bboxCentre;
      const latR = HOME_LAT * Math.PI / 180, lngR = HOME_LNG * Math.PI / 180;
      // Standard convention to match the lattice's new labelling:
      //   x = cos(lat)*sin(lng): east positive
      //   z = cos(lat)*cos(lng): prime meridian at +z (front)
      const star = {
        x: Math.cos(latR) * Math.sin(lngR),
        y: Math.sin(latR),
        z: Math.cos(latR) * Math.cos(lngR),
      };

      // Health motif geometry (built once) · a hairline national outline + an
      // ECG "vital sign" trace across the host country, projected per-frame like the star.
      type Sph = { x: number; y: number; z: number };
      const homeOutline: Sph[][] = homePolys.map(p => {
        const ring = p.rings[0]; const pts: Sph[] = [];
        for (let i = 0; i < ring.length; i += 6) pts.push(toXYZ(ring[i][0], ring[i][1]));
        return pts;
      });
      const ecg: Sph[] = (() => {
        const cLat = (homeBBox[1] + homeBBox[3]) / 2;
        // inset the trace from the edges by 8% of the span rather than a fixed
        // 2 degrees, which would invert the line on a small country
        const inset = Math.max(0.2, (homeBBox[2] - homeBBox[0]) * 0.08);
        const x0 = homeBBox[0] + inset, x1 = homeBBox[2] - inset, STEPS = 56;
        const bump = (u: number, c: number, w: number, a: number) => a * Math.exp(-((u - c) * (u - c)) / (2 * w * w));
        // flat baseline · P bump · sharp QRS complex · T bump (offset in degrees lat)
        const off = (u: number) => bump(u, 0.42, 0.03, 0.35)
          + bump(u, 0.485, 0.012, -0.5) + bump(u, 0.5, 0.008, 1.7) + bump(u, 0.515, 0.012, -0.75)
          + bump(u, 0.62, 0.04, 0.5);
        const pts: Sph[] = [];
        for (let i = 0; i < STEPS; i++) {
          const u = i / (STEPS - 1);
          pts.push(toXYZ(x0 + (x1 - x0) * u, cLat + off(u)));
        }
        return pts;
      })();

      // Pick up the accent colour from CSS · stays in sync with --accent
      const accentHex = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#0374b8').trim();
      const accentRGB = (() => {
        const m = accentHex.match(/#([0-9a-f]{6})/i);
        if (!m) return [3, 116, 184];
        const v = parseInt(m[1], 16);
        return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
      })();

      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      // Start with the host country facing the viewer. Its opening rotation is
      // simply its centre longitude: positive rotY rotates west-to-east,
      // matching Earth.
      let rotY = HOME_LNG * Math.PI / 180;
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
        // navy chrome and from the accent-blue host marker, not flag
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
          let r: number, fill: string;
          if (p.home) {
            // Host country: dimmer near-white stipple so the ECG motif + outline read on top.
            r = 1.05 + depth * 0.7;
            fill = `rgba(210, 224, 246, ${0.42 + depth * 0.22})`;
          } else {
            // Rest of the world: the accent blue, slightly dimmed so the host
            // host country reads as the clear focal point.
            r = 1.1 + depth * 0.9;
            fill = `rgba(${accentRGB[0]}, ${accentRGB[1]}, ${accentRGB[2]}, ${0.5 + depth * 0.28})`;
          }
          ctx.fillStyle = fill;
          ctx.beginPath();
          ctx.arc(screenX, screenY, r, 0, Math.PI * 2);
          ctx.fill();
        }

        // Health motif · hairline national outline + ECG heartbeat, glued to
        // the host country, back-face culled, dimming as it rotates to the limb
        const proj = (p: Sph) => {
          const rx = p.x * cosY - p.z * sinY;
          const rz = p.x * sinY + p.z * cosY;
          const ty = p.y * cosT - rz * sinT;
          const tz = p.y * sinT + rz * cosT;
          return { sx: cx + rx * radius, sy: cy - ty * radius, tz };
        };
        const strokePath = (pts: Sph[], color: string, width: number) => {
          ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
          ctx.beginPath();
          let pen = false;
          for (let i = 0; i < pts.length; i++) {
            const q = proj(pts[i]);
            if (q.tz < 0.02) { pen = false; continue; }   // back of sphere: lift pen
            if (!pen) { ctx.moveTo(q.sx, q.sy); pen = true; } else ctx.lineTo(q.sx, q.sy);
          }
          ctx.stroke();
        };
        for (const ring of homeOutline) strokePath(ring, 'rgba(232,241,255,0.55)', 1.1);
        strokePath(ecg, 'rgba(240,246,255,0.5)', 1.1);
        if (!reduced && ecg.length > 2) {
          const head = ((now || performance.now()) / 3400) % 1;
          const hi = Math.round(head * (ecg.length - 1));
          strokePath(ecg.slice(Math.max(0, hi - 8), hi + 1), 'rgba(228,238,255,0.9)', 1.5);
          const hp = proj(ecg[hi]);
          if (hp.tz >= 0.02) {
            const g = ctx.createRadialGradient(hp.sx, hp.sy, 0, hp.sx, hp.sy, 7);
            g.addColorStop(0, `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},0.85)`);
            g.addColorStop(1, `rgba(${accentRGB[0]},${accentRGB[1]},${accentRGB[2]},0)`);
            ctx.fillStyle = g;
            ctx.beginPath(); ctx.arc(hp.sx, hp.sy, 7, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = 'rgba(255,255,255,0.95)';
            ctx.beginPath(); ctx.arc(hp.sx, hp.sy, 1.6, 0, Math.PI * 2); ctx.fill();
          }
        }

        // Host-country marker · single pulsing star drawn on top
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
      draw(performance.now());
}
