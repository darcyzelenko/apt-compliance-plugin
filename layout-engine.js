/* ============================================================================
   layout-engine.js  —  Apartment Party-Wall Generation Engine  v0.1
   Implements the method in "Apartment Party-Wall Generation Algorithm" (V1 +
   key V2 transformations), minus the LLM layer.

   Topology-first: allocate facade by value -> grow apartment regions -> place
   room-zone templates -> detect conflicts -> apply a constrained party-wall
   transformation grammar -> score (multi-objective) -> validate (hard) -> rank.

   Pure + framework-agnostic: no DOM, no globals. Runs in the browser and in
   Node (for headless tests). FORMWORK feeds it rows; it returns ranked layouts.
   ========================================================================== */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LayoutEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const GRID = 0.6;
  const snap = (v, g) => Math.round(v / (g || GRID)) * (g || GRID);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const rect = (t, d, wt, wd, kind, label) => ({ t, d, wt, wd, kind, label });

  /* ---- default constraints / weights (all overridable per call) ---------- */
  const DEFAULTS = {
    constraints: {
      grid: GRID,
      areas: { b1: 50, b2: 70, b3: 90 },   // target NLA per type (m²)
      areaTol: 0.18,                        // ± fraction still "on target"
      minLivingFrontage: 3.6,               // living wants this much facade (m)
      minBedFrontage: 2.4,                  // a normal (non-snorkel) bed
      minBedNeck: 1.6,                      // snorkel neck min facade contact
      minRoomW: 2.4,                        // hard min room dimension
      maxRoomAspect: 3.2,                   // hard furnishability proxy
      bedDepth: 3.3, podW: 2.4, podD: 2.4, kitW: 1.8, kitD: 2.6, entryD: 1.4,
      preferredLivingDepthFrac: 0.95,       // living occupied depth proxy
      maxPartyCorners: 2                    // beyond this = over-articulated
    },
    weights: { daylight: 1.0, planning: 1.0, services: 0.7, partywall: 0.8, building: 0.6 }
  };

  /* ========================================================================
     0. GEOMETRY HELPERS (rectilinear; deterministic)
     ====================================================================== */
  function polyArea(p) { let a = 0; for (let i = 0, n = p.length; i < n; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % n]; a += x1 * y2 - x2 * y1; } return Math.abs(a) / 2; }
  function pointInPoly(px, py, poly) { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1]; if (((yi > py) !== (yj > py)) && (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) c = !c; } return c; }
  function bbox(poly) { let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; poly.forEach(([x, y]) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }); return { x0, y0, x1, y1, w: x1 - x0, d: y1 - y0 }; }
  function clipPolyByRect(poly, x0, y0, x1, y1) {
    let out = poly.slice();
    const step = (inside, ix) => { const r = []; for (let i = 0; i < out.length; i++) { const A = out[i], B = out[(i + 1) % out.length], ina = inside(A), inb = inside(B); if (ina) { r.push(A); if (!inb) r.push(ix(A, B)); } else if (inb) r.push(ix(A, B)); } out = r; };
    step(p => p[0] >= x0, (A, B) => { const t = (x0 - A[0]) / (B[0] - A[0]); return [x0, A[1] + t * (B[1] - A[1])]; });
    step(p => p[0] <= x1, (A, B) => { const t = (x1 - A[0]) / (B[0] - A[0]); return [x1, A[1] + t * (B[1] - A[1])]; });
    step(p => p[1] >= y0, (A, B) => { const t = (y0 - A[1]) / (B[1] - A[1]); return [A[0] + t * (B[0] - A[0]), y0]; });
    step(p => p[1] <= y1, (A, B) => { const t = (y1 - A[1]) / (B[1] - A[1]); return [A[0] + t * (B[0] - A[0]), y1]; });
    return out;
  }
  // inward offset of a polygon by a uniform distance (per-edge; convex-exact, concave-approx)
  function offsetInward(poly, dist) {
    const n = poly.length, lines = [];
    // assume CCW; inward normal = rotate edge dir by +90 (left). Detect winding.
    let area2 = 0; for (let i = 0; i < n; i++) { const [x1, y1] = poly[i], [x2, y2] = poly[(i + 1) % n]; area2 += x1 * y2 - x2 * y1; }
    const ccw = area2 > 0;
    for (let i = 0; i < n; i++) {
      const a = poly[i], b = poly[(i + 1) % n];
      let dx = b[0] - a[0], dy = b[1] - a[1]; const L = Math.hypot(dx, dy) || 1; dx /= L; dy /= L;
      // inward normal
      let nx = ccw ? dy : -dy, ny = ccw ? -dx : dx;
      lines.push({ px: a[0] + nx * dist, py: a[1] + ny * dist, dx, dy });
    }
    const out = [];
    for (let i = 0; i < n; i++) {
      const L1 = lines[(i - 1 + n) % n], L2 = lines[i];
      const det = L1.dx * (-L2.dy) - L1.dy * (-L2.dx);
      if (Math.abs(det) < 1e-9) { out.push([L2.px, L2.py]); continue; }
      const ex = L2.px - L1.px, ey = L2.py - L1.py;
      const t = (ex * (-L2.dy) - ey * (-L2.dx)) / det;
      out.push([L1.px + L1.dx * t, L1.py + L1.dy * t]);
    }
    return out;
  }
  // grid-snap + drop short edges + orthogonalise near-axis edges
  function simplifyOrtho(poly, minEdge, g) {
    g = g || GRID;
    let p = poly.map(([x, y]) => [snap(x, g), snap(y, g)]);
    // orthogonalise: if an edge is within ~12° of an axis, snap the lesser coord
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length];
      const dx = b[0] - a[0], dy = b[1] - a[1];
      if (Math.abs(dx) > 1e-6 && Math.abs(dy) > 1e-6) {
        if (Math.abs(dy) < Math.abs(dx) * 0.21) b[1] = a[1];          // ~near-horizontal
        else if (Math.abs(dx) < Math.abs(dy) * 0.21) b[0] = a[0];     // ~near-vertical
      }
    }
    // drop near-duplicate / very short edges
    const out = [];
    for (let i = 0; i < p.length; i++) { const a = p[i], b = out.length ? out[out.length - 1] : null;
      if (!b || Math.hypot(a[0] - b[0], a[1] - b[1]) >= (minEdge || g)) out.push(a); }
    // merge collinear
    const merged = [];
    for (let i = 0; i < out.length; i++) {
      const prev = out[(i - 1 + out.length) % out.length], cur = out[i], next = out[(i + 1) % out.length];
      const cx = (cur[0] - prev[0]), cy = (cur[1] - prev[1]), nx = (next[0] - cur[0]), ny = (next[1] - cur[1]);
      if (Math.abs(cx * ny - cy * nx) > 1e-6) merged.push(cur);   // keep only true corners
    }
    return merged.length >= 3 ? merged : out;
  }

  /* ========================================================================
     1. FLOORPLATE PREPARATION — three modes
        rect:     bounding box inset by setbacks (most conservative)  [current]
        cleaned:  setback-offset boundary, simplified + grid-snapped  [middle]
        boundary: setback-offset boundary, jogs preserved (tightest fit)
     ====================================================================== */
  function prepareFloorplate(boundary, opts) {
    opts = opts || {};
    const mode = opts.mode || 'cleaned';
    const sb = opts.setbacks || { front: 0, rear: 0, left: 0, right: 0 };
    const g = opts.grid || GRID;
    const bb = bbox(boundary);
    if (mode === 'rect') {
      return { mode, poly: [
        [bb.x0 + (sb.left || 0), bb.y0 + (sb.front || 0)],
        [bb.x1 - (sb.right || 0), bb.y0 + (sb.front || 0)],
        [bb.x1 - (sb.right || 0), bb.y1 - (sb.rear || 0)],
        [bb.x0 + (sb.left || 0), bb.y1 - (sb.rear || 0)] ] };
    }
    // uniform inset = average setback (per-edge directional setbacks need an
    // oriented boundary; uniform offset is the deterministic prototype baseline)
    const inset = (sb.front + sb.rear + sb.left + sb.right) / 4 || (sb.left || 3);
    let off = offsetInward(boundary, inset);
    // guard against self-intersection collapse on concave lots -> fall back to inset rect
    if (off.length < 3 || polyArea(off) < 1) {
      off = [[bb.x0 + inset, bb.y0 + inset], [bb.x1 - inset, bb.y0 + inset], [bb.x1 - inset, bb.y1 - inset], [bb.x0 + inset, bb.y1 - inset]];
    }
    const poly = (mode === 'cleaned') ? simplifyOrtho(off, g * 3, g) : off.map(([x, y]) => [snap(x, g), snap(y, g)]);
    return { mode, poly };
  }

  /* ========================================================================
     2. ROW DERIVATION — turn a rectangular plate + corridor into facade rows.
        Each row = one facade line with depth -> corridor. (Double-loaded => 2
        rows; single-loaded => 1.) FORMWORK can pass its own rows instead.
     ====================================================================== */
  function rowsFromPlate(plate, P) {
    const bb = bbox(plate.poly), corr = P.corridor || 1.8;
    const alongX = bb.w >= bb.d, L = alongX ? bb.w : bb.d, Dtot = alongX ? bb.d : bb.w;
    const minD = P.minDepth || 7, maxD = P.maxDepth || 8.5;
    const double = (Dtot - corr) / 2 >= minD;
    const aptD = double ? clamp((Dtot - corr) / 2, minD, maxD) : clamp(Dtot - corr, minD, maxD);
    const rows = [];
    // daylightValue from orientation: north-facing facades score high (S. hemisphere)
    const north = P.northVec || [0, 1];
    const mk = (origin, tAxis, nrm) => {
      const dv = clamp(0.5 + 0.5 * (nrm[0] * north[0] + nrm[1] * north[1]), 0.12, 1);
      rows.push({ origin, tAxis, dAxis: [-nrm[0], -nrm[1]], length: L, depth: aptD, normal: nrm, daylightValue: dv });
    };
    if (alongX) {
      mk([bb.x0, bb.y0], [1, 0], [0, -1]);                       // front row (faces -y)
      if (double) mk([bb.x0, bb.y1], [1, 0], [0, 1]);            // rear row (faces +y)
    } else {
      mk([bb.x0, bb.y0], [0, 1], [-1, 0]);
      if (double) mk([bb.x1, bb.y0], [0, 1], [1, 0]);
    }
    return { rows, aptD, corr, double, alongX, L, Dtot, bb };
  }

  /* ========================================================================
     3. FACADE GRAPH — segment a row's facade, classify by value.
     ====================================================================== */
  function facadeGraph(row, P) {
    const segLen = P.facadeModule || 1.2, segs = [];
    let t = 0; while (t < row.length - 1e-6) { const w = Math.min(segLen, row.length - t);
      segs.push({ t0: t, t1: t + w, value: row.daylightValue }); t += w; }
    const cls = row.daylightValue > 0.66 ? 'primary' : (row.daylightValue > 0.4 ? 'secondary' : 'tertiary');
    return { segments: segs, facadeClass: cls, value: row.daylightValue };
  }

  /* ========================================================================
     4. APARTMENT SEQUENCING — claim facade widths to hit the mix (area/depth),
        same economy as a bar tiler but here it only sets the *claim*; room
        value-allocation + party-wall negotiation happen afterwards.
     ====================================================================== */
  function sequence(row, P, counts) {
    const C = P.constraints, shares = P.shares, out = [];
    const fr = t => snap(clamp(C.areas[t] / row.depth, 3, 12));
    let t = 0;
    while (t < row.length - 1e-6) {
      const tot = counts.b1 + counts.b2 + counts.b3;
      const order = ['b1', 'b2', 'b3'].sort((a, b) =>
        ((tot ? counts[a] / tot : 0) - shares[a]) - ((tot ? counts[b] / tot : 0) - shares[b]));
      let pick = null;
      for (const ty of order) { if (shares[ty] > 0 && t + fr(ty) <= row.length + 1e-6) { pick = ty; break; } }
      if (!pick) for (const ty of ['b1', 'b2', 'b3']) { if (t + fr(ty) <= row.length + 1e-6) { pick = ty; break; } }
      if (!pick) break;
      const w = fr(pick);
      out.push({ type: pick, t0: t, t1: t + w, depth: row.depth, daylightValue: row.daylightValue });
      counts[pick]++; t += w;
    }
    return out;
  }

  /* ========================================================================
     5. ROOM-ZONE TEMPLATE — value-driven (living gets facade, beds may snorkel,
        wet zones deep, kitchen perpendicular & adjacent to the pod).
        Local frame per apartment: u along facade (0..W), d inward (0..D).
     ====================================================================== */
  function roomTemplate(apt, P, strategy) {
    const C = P.constraints, W = apt.t1 - apt.t0, D = apt.depth, nBed = { b1: 1, b2: 2, b3: 3 }[apt.type] || 1;
    const rooms = [], windows = [], doors = [], warnings = [];
    let bd = clamp(0.42 * D, 3.0, 3.9); bd = Math.min(bd, D - 2.6);
    // FACADE SPLIT BY STRATEGY (this is the heart of the method):
    //  straight        — even split living vs bedrooms (often buries the living room)
    //  living_priority — protect living frontage first; bedrooms take the minimum
    let LW, BW, snorkel = false;
    if (strategy === 'straight') {
      LW = W / (nBed + 1); BW = W - LW;                       // living = one equal share
    } else {
      // living-priority: protect living, but beds must always be ≥ minBedFrontage
      LW = Math.max(C.minLivingFrontage, W - nBed * C.minBedFrontage); BW = W - LW;
      // if unit is too narrow for both at minimum, give beds their floor and accept less living
      if (BW / nBed < C.minBedFrontage) { BW = nBed * C.minBedFrontage; LW = Math.max(C.minRoomW, W - BW); }
      // snorkel (narrow neck + wider body) is a V2 feature — body not yet modelled; disabled for now
      snorkel = false;
    }
    const bedW = Math.max(C.minBedFrontage, BW / nBed);
    for (let i = 0; i < nBed; i++) {
      rooms.push(rect(i * bedW, 0, bedW, bd, 'bed', snorkel ? 'BED~' : 'BED'));
      windows.push({ t0: i * bedW + 0.3, t1: (i + 1) * bedW - 0.3, kind: 'bed' });
      doors.push({ t: i * bedW + bedW / 2, d: bd });
    }
    windows.push({ t0: BW + 0.4, t1: W - 0.4, kind: 'living' });
    const podW = Math.min(C.podW, BW), podT = Math.max(0, BW - podW);
    const svcD = Math.min(C.podD, D * 0.28);
    const svcStart = D - svcD;                        // directly at corridor wall
    rooms.push(rect(podT, svcStart, podW, svcD, 'pod', 'POD'));
    doors.push({ t: podT + podW * 0.5, d: svcStart });
    rooms.push(rect(BW, Math.max(svcStart, D - C.kitD), C.kitW, C.kitD, 'kitchen', 'KIT'));
    const entW = Math.min(1.6, BW);
    doors.push({ t: entW * 0.5, d: D });
    return { rooms, windows, doors, snorkel, livingFrontage: LW, bedFrontage: BW, bedDepth: bd, svcStart };
  }

  /* ========================================================================
     6. CONFLICT DETECTION  (§8)
     ====================================================================== */
  function detectConflicts(apt, tmpl, P) {
    const C = P.constraints, c = [];
    const living = tmpl.rooms.find(r => r.kind === 'living');
    const beds = tmpl.rooms.filter(r => r.kind === 'bed');
    const pod = tmpl.rooms.find(r => r.kind === 'pod');
    const kit = tmpl.rooms.find(r => r.kind === 'kitchen');
    if (tmpl.livingFrontage < C.minLivingFrontage - 1e-6) c.push({ type: 'buried_living', sev: 2, msg: `living frontage ${tmpl.livingFrontage.toFixed(1)}m < ${C.minLivingFrontage}m` });
    beds.forEach((b, i) => { if (b.wt > tmpl.livingFrontage + 1e-6) c.push({ type: 'bed_on_primary', sev: 2, msg: `bedroom ${i + 1} (${b.wt.toFixed(1)}m) takes more facade than living` }); });
    if (kit && kit.d < 0.6) c.push({ type: 'kitchen_on_facade', sev: 1, msg: 'kitchen against facade' });
    if (pod && pod.d < tmpl.bedDepth - 1e-6) c.push({ type: 'pod_near_facade', sev: 2, msg: 'pod too close to facade' });
    tmpl.rooms.forEach(r => { const lo = Math.min(r.wt, r.wd), hi = Math.max(r.wt, r.wd);
      if (lo < C.minRoomW - 1e-6 && (r.kind === 'living' || (r.kind === 'bed' && !tmpl.snorkel)))
        c.push({ type: 'unfurnishable', sev: 3, msg: `${r.kind} min dim ${lo.toFixed(1)}m < ${C.minRoomW}m` });
      else if (lo < C.minRoomW - 1e-6 && r.kind === 'bed' && tmpl.snorkel)
        c.push({ type: 'snorkel_neck', sev: 1, msg: `snorkel bed neck ${lo.toFixed(1)}m (deep body not yet modelled)` });
      if ((r.kind === 'bed' || r.kind === 'living') && lo > 0 && hi / lo > C.maxRoomAspect)
        c.push({ type: 'unfurnishable', sev: 1, msg: `${r.kind} aspect ${(hi / lo).toFixed(1)} > ${C.maxRoomAspect}` }); });
    return c;
  }

  /* ========================================================================
     7. PARTY-WALL TRANSFORMATION GRAMMAR (§7)  — applied across a row.
        Implemented: straight (baseline), snorkel (intra, via template),
        step (inter), s-wall (inter, reciprocal), wet-zone interlock (inter).
        Each transform mutates apartment facade claims / pod placement, then
        room templates are re-run. Articulation carries a construction penalty.
     ====================================================================== */
  function negotiate(apts, P) {
    const C = P.constraints;
    for (let i = 0; i < apts.length - 1; i++) {
      const a = apts[i], b = apts[i + 1];
      const aBuried = a.tmpl.livingFrontage < C.minLivingFrontage - 1e-6;
      const bBuried = b.tmpl.livingFrontage < C.minLivingFrontage - 1e-6;
      // STEP / S-WALL: a buried apartment borrows facade columns from a neighbour
      // in the shallow (facade) band; the neighbour is compensated deep -> S-wall.
      if (aBuried && !bBuried && (b.t1 - b.t0) > C.minLivingFrontage + C.minBedFrontage) {
        const give = Math.min(0.6, (b.t1 - b.t0) - (C.minLivingFrontage + C.minBedFrontage));
        if (give >= GRID) {
          a.step = { side: 'right', shallow: give, band: a.tmpl.bedDepth }; // a gains facade in shallow band
          b.step = { side: 'left', shallow: -give, band: b.tmpl.bedDepth };  // b loses facade shallow (compensated deep)
          a.articulation = 's-wall'; b.articulation = 's-wall';
        }
      }
      // WET-ZONE INTERLOCK: adjacent pods placed back-to-back across the party wall
      const pa = a.tmpl.rooms.find(r => r.kind === 'pod'), pb = b.tmpl.rooms.find(r => r.kind === 'pod');
      if (pa && pb) { pa.t = (a.t1 - a.t0) - pa.wt - 0.0; pb.t = 0; a.interlock = b.interlock = true; }
    }
    return apts;
  }

  /* ========================================================================
     8. SCORING (§9) + 9. HARD VALIDATION (§13)
     ====================================================================== */
  function evaluate(apt, P) {
    const C = P.constraints, W = P.weights, t = apt.tmpl, dv = apt.daylightValue;
    const target = C.areas[apt.type], area = (apt.t1 - apt.t0) * apt.depth;
    const living = t.rooms.find(r => r.kind === 'living'), beds = t.rooms.filter(r => r.kind === 'bed');
    const kit = t.rooms.find(r => r.kind === 'kitchen'), pod = t.rooms.find(r => r.kind === 'pod');
    // sub-scores 0..1
    const sDaylight = clamp(t.livingFrontage / Math.max(C.minLivingFrontage, 1), 0, 1.2) / 1.2 * (0.5 + 0.5 * dv) * (t.snorkel ? 0.95 : 1);
    const sPlan = clamp(1 - Math.abs(area - target) / (target * C.areaTol || 1) * 0.5, 0, 1);
    const sServices = (pod && pod.d >= t.bedDepth ? 0.6 : 0.2) + (kit && kit.d >= 0.6 ? 0.4 : 0) + (apt.interlock ? 0.0 : 0);
    const corners = apt.step ? 2 : 0;
    const sParty = clamp(1 - corners / (C.maxPartyCorners + 1) * 0.5 - (apt.articulation === 's-wall' ? 0.1 : 0), 0, 1);
    const sBuild = clamp(1 - Math.abs(area - target) / target, 0, 1);
    const conflicts = detectConflicts(apt, t, P);
    const hard = conflicts.filter(c => c.sev >= 3);
    const valid = hard.length === 0 && t.livingFrontage > 0 && beds.every(b => b.wt > 0);
    const score = 100 * (W.daylight * sDaylight + W.planning * sPlan + W.services * Math.min(1, sServices) + W.partywall * sParty + W.building * sBuild)
      / (W.daylight + W.planning + W.services + W.partywall + W.building);
    return { score: valid ? score : score * 0.4, valid, area, conflicts,
      breakdown: { daylight: +sDaylight.toFixed(2), planning: +sPlan.toFixed(2), services: +Math.min(1, sServices).toFixed(2), partywall: +sParty.toFixed(2), building: +sBuild.toFixed(2) },
      explain: conflicts.map(c => c.msg) };
  }

  /* ========================================================================
     10. CANDIDATE GENERATION + RANKING (§5 workflow, §10 outputs)
     ====================================================================== */
  const STRATEGIES = ['straight', 'living_priority', 'negotiated'];
  function layoutRowStrategy(row, P, strategy, counts) {
    const apts = sequence(row, P, Object.assign({}, counts));
    apts.forEach(a => { a.tmpl = roomTemplate(a, P, strategy); a.daylightValue = row.daylightValue; });
    if (strategy === 'negotiated') { negotiate(apts, P); apts.forEach(a => { /* re-template after step */
      if (a.step) { a.tmpl = roomTemplate(a, P, 'living_priority'); } }); }
    apts.forEach(a => { a.eval = evaluate(a, P); });
    return apts;
  }
  function scoreRow(apts) {
    const valids = apts.filter(a => a.eval.valid).length;
    const mean = apts.reduce((s, a) => s + a.eval.score, 0) / Math.max(1, apts.length);
    return { score: mean, valid: valids === apts.length, validCount: valids, total: apts.length };
  }

  // public: lay out a single row, returning ranked strategy options
  function layoutRow(row, P) {
    P = mergeP(P);
    const options = STRATEGIES.map(st => {
      const counts = { b1: 0, b2: 0, b3: 0 };
      const apts = layoutRowStrategy(row, P, st, counts);
      const rs = scoreRow(apts);
      return { strategy: st, apartments: apts, score: +rs.score.toFixed(1), valid: rs.valid, validCount: rs.validCount, total: rs.total,
        warnings: apts.flatMap(a => a.eval.explain) };
    });
    options.sort((a, b) => (b.valid - a.valid) || (b.score - a.score));
    return { options, best: options[0] };
  }

  /* ========================================================================
     11. TOP-LEVEL: lay out a whole floorplate (all rows), ranked.
     ====================================================================== */
  function mergeP(P) {
    P = P || {};
    const C = Object.assign({}, DEFAULTS.constraints, P.constraints || {});
    const W = Object.assign({}, DEFAULTS.weights, P.weights || {});
    const shares = normShares(P.mix || { b1: 0.3, b2: 0.5, b3: 0.2 });
    return Object.assign({}, P, { constraints: C, weights: W, shares });
  }
  function normShares(m) { const s = (m.b1 || 0) + (m.b2 || 0) + (m.b3 || 0) || 1; return { b1: (m.b1 || 0) / s, b2: (m.b2 || 0) / s, b3: (m.b3 || 0) / s }; }

  function generateLayout(input) {
    const P = mergeP(input);
    const plate = input.plate || prepareFloorplate(input.boundary || [[0, 0], [30, 0], [30, 38], [0, 38]],
      { mode: input.plateMode || 'cleaned', setbacks: input.setbacks, grid: P.constraints.grid });
    const der = rowsFromPlate({ poly: plate.poly }, {
      corridor: input.corridor || 1.8, minDepth: input.minDepth, maxDepth: input.maxDepth,
      northVec: input.northVec
    });
    // choose the best strategy per row, then assemble; also report alternatives
    const counts = { b1: 0, b2: 0, b3: 0 };
    const rows = der.rows.map((row, i) => {
      const fg = facadeGraph(row, P);
      const ranked = layoutRow(row, P);
      const best = ranked.best;
      best.apartments.forEach(a => counts[a.type]++);   // tally chosen
      return { index: i, facade: fg, normal: row.normal, daylightValue: row.daylightValue,
        origin: row.origin, tAxis: row.tAxis, dAxis: row.dAxis, depth: row.depth, length: row.length,
        chosen: best, options: ranked.options };
    });
    const total = counts.b1 + counts.b2 + counts.b3;
    const achievedMix = { b1: total ? counts.b1 / total : 0, b2: total ? counts.b2 / total : 0, b3: total ? counts.b3 / total : 0 };
    const score = rows.reduce((s, r) => s + r.chosen.score, 0) / Math.max(1, rows.length);
    const warnings = []; ['b1', 'b2', 'b3'].forEach(t => { const d = Math.abs(achievedMix[t] - P.shares[t]); if (d > 0.12) warnings.push(`${t.replace('b', '')}-bed mix ${(achievedMix[t] * 100) | 0}% vs target ${(P.shares[t] * 100) | 0}%`); });
    return {
      version: '0.1', plate, corridor: der.corr, double: der.double, alongX: der.alongX,
      rows, counts, total, achievedMix, targetMix: P.shares, score: +score.toFixed(1),
      warnings: warnings.concat(rows.flatMap(r => r.chosen.warnings)),
      meta: { strategies: STRATEGIES, constraints: P.constraints, weights: P.weights }
    };
  }

  /* ========================================================================
     12. WORLD MAPPING — turn a row's local (t,d) geometry into world coords,
         honouring step/s-wall footprint changes. Returns render-ready rects.
     ====================================================================== */
  function L2Wrect(row, t, d, wt, wd) {
    const [ox, oy] = row.origin, [tx, ty] = row.tAxis, [dx, dy] = row.dAxis;
    // corner at (t,d); extents wt along tAxis, wd along dAxis
    const x0 = ox + tx * t + dx * d, y0 = oy + ty * t + dy * d;
    const xs = [x0, x0 + tx * wt, x0 + dx * wd, x0 + tx * wt + dx * wd];
    const ys = [y0, y0 + ty * wt, y0 + dy * wd, y0 + ty * wt + dy * wd];
    const X = Math.min(...xs), Y = Math.min(...ys);
    return { x: X, y: Y, wx: Math.max(...xs) - X, wy: Math.max(...ys) - Y };
  }
  // expand a chosen row option into world apartments + rooms (for rendering/export)
  function realiseRowWorld(row) {
    const apts = row.chosen.apartments.map(a => {
      const W = a.t1 - a.t0;
      const footprint = [L2Wrect(row, a.t0, 0, W, a.depth)];                 // base footprint
      if (a.step) {                                                          // s-wall: shallow band shifts
        const g = a.step.shallow, band = a.step.band;
        footprint[0] = L2Wrect(row, a.t0, band, W, a.depth - band);          // deep band keeps base width
        footprint.push(L2Wrect(row, a.t0 + (a.step.side === 'left' ? Math.max(0, -g) : 0), 0,
          W + (a.step.side === 'right' ? g : -Math.max(0, g)), band));       // shallow band ± g
      }
      const rooms = a.tmpl.rooms.map(r => Object.assign(L2Wrect(row, a.t0 + r.t, r.d, r.wt, r.wd), { kind: r.kind, label: r.label }));
      const windows = a.tmpl.windows.map(w => ({ a: rowPt(row, a.t0 + w.t0, 0), b: rowPt(row, a.t0 + w.t1, 0), kind: w.kind }));
      const doors = a.tmpl.doors.map(dd => ({ p: rowPt(row, a.t0 + dd.t, dd.d), n: row.normal }));
      return { type: a.type, footprint, rooms, windows, doors, nrm: row.normal, score: a.eval.score,
        valid: a.eval.valid, breakdown: a.eval.breakdown, conflicts: a.eval.conflicts, articulation: a.articulation || 'straight',
        snorkel: a.tmpl.snorkel, interlock: !!a.interlock };
    });
    return apts;
  }
  function rowPt(row, t, d) { const [ox, oy] = row.origin, [tx, ty] = row.tAxis, [dx, dy] = row.dAxis; return [ox + tx * t + dx * d, oy + ty * t + dy * d]; }

  /* ========================================================================
     13. FORMWORK ADAPTER — lay out a single apartment of given width/depth,
         comparing strategies and returning the best, in the same local shape
         FORMWORK's layoutApartment() returns, plus score/validity/conflicts.
     ====================================================================== */
  function layoutApartmentBest(W, D, type, daylightValue, opts) {
    const P = mergeP(opts || {});
    const apt = { type: type, t0: 0, t1: W, depth: D, daylightValue: (daylightValue == null ? 0.6 : daylightValue) };
    let best = null;
    ['straight', 'living_priority'].forEach(st => {
      const tmpl = roomTemplate(apt, P, st);
      const ev = evaluate(Object.assign({}, apt, { tmpl: tmpl }), P);
      const better = !best || (ev.valid && !best.ev.valid) || (ev.valid === best.ev.valid && ev.score > best.ev.score);
      if (better) best = { strategy: st, tmpl: tmpl, ev: ev };
    });
    const t = best.tmpl;
    return {
      rooms: t.rooms, windows: t.windows, doors: t.doors, snorkel: t.snorkel,
      livingFrontage: t.livingFrontage, svcStart: t.svcStart,
      strategy: best.strategy, score: +best.ev.score.toFixed(1),
      valid: best.ev.valid, conflicts: best.ev.conflicts, breakdown: best.ev.breakdown
    };
  }

  return {
    version: '0.1', GRID,
    prepareFloorplate, rowsFromPlate, facadeGraph, sequence, roomTemplate,
    detectConflicts, negotiate, evaluate, layoutRow, generateLayout,
    realiseRowWorld, L2Wrect, rowPt, layoutApartmentBest,
    STRATEGIES, DEFAULTS
  };
});