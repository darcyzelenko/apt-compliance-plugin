/* ============================================================================
   floorplate-engine.js  —  Floor Plate Generation Engine  v0.1

   Generates apartment floor plan layouts from site dimensions, setbacks,
   typology, mix and core configuration.

   Pure / stateless: no DOM, no Three.js, no room-layout (that stays in the
   host app via attachServices / layout-engine). Returns raw unit footprints
   with world positions so the host can post-process rooms on correct widths.

   UMD: works as browser global (FloorplateEngine) or Node require().
   ============================================================================ */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FloorplateEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const GRID = 0.6;
  const snap  = (v, g) => Math.round(v / (g || GRID)) * (g || GRID);
  const floorG = v => Math.floor(v / GRID + 1e-6) * GRID;
  const clamp  = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /* ── Typology definitions ────────────────────────────────────────────────── */
  const DEV_KINDS = {
    double_loaded : { label: 'Double-loaded corridor', core: 'blade', circ: 'central'  },
    single_loaded : { label: 'Single-loaded corridor', core: 'blade', circ: 'side'     },
    multi_core    : { label: 'Multi-core',             core: 'blade', circ: 'central'  },
    point_stair   : { label: 'Point stair',            core: 'point', circ: 'lobby'    },
    through_double: { label: 'Through double',         core: 'blade', circ: 'central'  },
    courtyard     : { label: 'Courtyard',              core: 'point', circ: 'gallery'  },
  };

  /* ── Geometry primitives ─────────────────────────────────────────────────── */
  const R = (x, y, wx, wy, kind) => ({ x, y, wx, wy, kind });

  function buildFrame(bx0, by0, bx1, by1) {
    let W = floorG(bx1 - bx0), D = floorG(by1 - by0);
    bx1 = bx0 + W; by1 = by0 + D;
    const alongX = W >= D;
    return { x0:bx0, y0:by0, x1:bx1, y1:by1, len:alongX?W:D, depth:alongX?D:W, alongX };
  }

  function mapRect(frame, u, v, du, dv, meta) {
    let x, y, wx, wy;
    if (frame.alongX) { x = frame.x0+u; y = frame.y0+v; wx = du; wy = dv; }
    else              { x = frame.x0+v; y = frame.y0+u; wx = dv; wy = du; }
    return Object.assign({ x, y, wx, wy }, meta);
  }

  /* ── Minimum viable unit width (facade direction) ───────────────────────── */
  const N_BEDS = { b1:1, b2:2, b3:3 };
  const minViableW = t => (N_BEDS[t] || 1) * 2.4 + 3.6;

  /* ── Orientation ─────────────────────────────────────────────────────────── */
  function northnessOf(nrm, northVec) {
    const n = northVec || [0, 1];
    return nrm[0]*n[0] + nrm[1]*n[1];
  }

  /* ── Tile a single row ───────────────────────────────────────────────────── */
  function tileRow(len, aptD, areas, shares, counts, frame, vOff, level, wscale) {
    wscale = wscale || 1;
    const fr = t => Math.max(
      snap(Math.min(14, Math.max(3, ((areas[t] || 70) / aptD) * wscale))),
      minViableW(t));
    const units = []; let u = 0;
    while (true) {
      const total = counts.b1 + counts.b2 + counts.b3;
      const order = ['b1', 'b2', 'b3'].sort((a, b) => {
        const da = (total ? counts[a]/total : 0) - shares[a];
        const db = (total ? counts[b]/total : 0) - shares[b];
        return da - db;
      });
      let placed = null;
      for (const t of order) {
        if (shares[t] <= 0) continue;
        if (u + fr(t) <= len + 1e-6) { placed = t; break; }
      }
      if (!placed) {
        for (const t of ['b1', 'b2', 'b3']) {
          if (shares[t] > 0 && u + fr(t) <= len + 1e-6) { placed = t; break; }
        }
      }
      if (!placed) break;
      const w = fr(placed);
      units.push(mapRect(frame, u, vOff, w, aptD, { type: placed, level }));
      counts[placed]++; u += w;
    }
    return units;
  }

  /* ── Flex fill — iterative proportional width distribution ──────────────── */
  function flexFill(units, spanLen, frame, aptD, areas) {
    if (!units.length || spanLen <= 0) return;
    const minWf = u => minViableW(u.type);
    const maxWf = u => Math.min(16, Math.max(minWf(u), ((areas[u.type] || 70) / aptD) * 2));
    let ww = units.map(u => frame.alongX ? u.wx : u.wy);
    for (let iter = 0; iter < 12; iter++) {
      const used = ww.reduce((a, b) => a + b, 0), rem = spanLen - used;
      if (Math.abs(rem) < 1e-4) break;
      const canFlex = ww.map((w, i) => rem > 0 ? w < maxWf(units[i]) - 1e-6 : w > minWf(units[i]) + 1e-6);
      const nf = canFlex.filter(Boolean).length; if (!nf) break;
      const d = rem / nf;
      ww = ww.map((w, i) => canFlex[i] ? Math.max(minWf(units[i]), Math.min(maxWf(units[i]), w + d)) : w);
    }
    const start = frame.alongX ? units[0].x : units[0].y; let pos = start;
    units.forEach((u, i) => {
      if (frame.alongX) { u.x = pos; u.wx = ww[i]; }
      else              { u.y = pos; u.wy = ww[i]; }
      pos += ww[i];
    });
  }

  /* ── S-wall party wall negotiation ──────────────────────────────────────── */
  function applySwalls(units, aptD, frame, rowNrm) {
    if (units.length < 2) return;
    const STEP = 1.2, BAND = aptD * 0.5;
    const horiz = rowNrm[1] !== 0;
    const facadeAtLow = horiz ? rowNrm[1] < 0 : rowNrm[0] < 0;
    for (let i = 0; i < units.length - 1; i++) {
      const A = units[i], B = units[i+1];
      const minWA = minViableW(A.type), minWB = minViableW(B.type);
      const Aw = horiz ? A.wx : A.wy, Bw = horiz ? B.wx : B.wy;
      if (Aw > minWA + 1.5 || Bw < minWB + STEP - 1e-6) continue;
      if (horiz) {
        const sY0 = facadeAtLow ? A.y : A.y + aptD - BAND;
        const dY0 = facadeAtLow ? A.y + BAND : A.y;
        A.footprint = [{ x:A.x,          y:sY0, wx:Aw+STEP, wy:BAND       },
                       { x:A.x,          y:dY0, wx:Aw-STEP, wy:aptD-BAND  }];
        B.footprint = [{ x:A.x+Aw+STEP,  y:sY0, wx:Bw-STEP, wy:BAND       },
                       { x:A.x+Aw-STEP,  y:dY0, wx:Bw+STEP, wy:aptD-BAND  }];
      } else {
        const sX0 = facadeAtLow ? A.x : A.x + aptD - BAND;
        const dX0 = facadeAtLow ? A.x + BAND : A.x;
        A.footprint = [{ x:sX0, y:A.y,          wx:BAND,      wy:Aw+STEP },
                       { x:dX0, y:A.y,          wx:aptD-BAND, wy:Aw-STEP }];
        B.footprint = [{ x:sX0, y:A.y+Aw+STEP,  wx:BAND,      wy:Bw-STEP },
                       { x:dX0, y:A.y+Aw-STEP,  wx:aptD-BAND, wy:Bw+STEP }];
      }
      A.shallowW = Aw + STEP;
      B.shallowW = Bw - STEP;
    }
  }

  /* ── Tile a courtyard side (simple row, no services) ────────────────────── */
  function tileWorldSide(ox, oy, dir, len, depth, inward, areas, shares, counts, level, nrm, wscale) {
    const fr = t => snap(Math.min(14, Math.max(3, ((areas[t]||70) / depth) * (wscale||1))));
    const out = []; let u = 0;
    while (true) {
      const tot = counts.b1 + counts.b2 + counts.b3;
      const order = ['b1','b2','b3'].sort((a,b) =>
        ((tot?counts[a]/tot:0)-shares[a]) - ((tot?counts[b]/tot:0)-shares[b]));
      let placed = null;
      for (const t of order) { if (shares[t]>0 && u+fr(t)<=len+1e-6) { placed=t; break; } }
      if (!placed) for (const t of ['b1','b2','b3']) { if (shares[t]>0 && u+fr(t)<=len+1e-6) { placed=t; break; } }
      if (!placed) break;
      const w = fr(placed);
      const ax = ox+dir[0]*u, ay = oy+dir[1]*u;
      const x0 = ax+Math.min(0,dir[0]*w)+Math.min(0,inward[0]*depth);
      const y0 = ay+Math.min(0,dir[1]*w)+Math.min(0,inward[1]*depth);
      const wx = Math.abs(dir[0]*w)+Math.abs(inward[0]*depth);
      const wy = Math.abs(dir[1]*w)+Math.abs(inward[1]*depth);
      out.push({ x:x0, y:y0, wx, wy, type:placed, level, nrm });
      counts[placed]++; u += w;
    }
    return out;
  }

  /* ── Layout one floor level ──────────────────────────────────────────────── */
  function layoutLevel(frame, opts, shares, counts, level, isSB, coreRef) {
    const {
      corr    = 1.8,
      coreLen = 6.0,
      devkind = 'double_loaded',
      northVec = [0, 1],
      areas   = { b1:50, b2:70, b3:90 },
      minD    = 6,
      maxD    = 10,
    } = opts;

    const kind = DEV_KINDS[devkind] || DEV_KINDS.double_loaded;
    const CR   = coreRef || frame;
    const northness = nrm => northnessOf(nrm, northVec);
    const vU   = frame.alongX ? [0,1] : [1,0];
    const negV = [-vU[0], -vU[1]];

    const canDouble = (frame.depth - corr) / 2 >= minD;
    const single    = (kind.circ === 'side') || !canDouble;
    const aptD      = single
      ? floorG(Math.min(maxD, frame.depth - corr))
      : floorG(Math.min(maxD, (frame.depth - corr) / 2));
    const usedDepth = single ? aptD + corr : 2 * aptD + corr;
    const dv0 = Math.max(0, (frame.depth - usedDepth) / 2);
    const eff = {
      alongX: frame.alongX, len: frame.len, depth: usedDepth,
      x0: frame.x0 + (frame.alongX ? 0 : dv0), y0: frame.y0 + (frame.alongX ? dv0 : 0),
      x1: frame.x1 - (frame.alongX ? 0 : dv0), y1: frame.y1 - (frame.alongX ? dv0 : 0),
    };
    const pulled = usedDepth < frame.depth - 1e-6;

    /* COURTYARD */
    if (devkind === 'courtyard') {
      const { x0:X0, y0:Y0, x1:X1, y1:Y1 } = frame;
      const W = X1-X0, Dp = Y1-Y0;
      const rd = floorG(Math.min(maxD, Math.max(minD, 7.8)));
      if (W - 2*rd >= minD && Dp - 2*rd >= minD) {
        let units = [];
        const nOpts = { areas, northVec };
        const ws = nrm => 1 - 0.18 * northnessOf(nrm, northVec);
        units = units.concat(tileWorldSide(X0+rd,Y0, [1,0], W-2*rd, rd, [0,1],  areas,shares,counts,level,[0,-1],ws([0,-1])));
        units = units.concat(tileWorldSide(X0+rd,Y1, [1,0], W-2*rd, rd, [0,-1], areas,shares,counts,level,[0, 1],ws([0, 1])));
        units = units.concat(tileWorldSide(X0,Y0+rd,  [0,1], Dp-2*rd,rd, [1,0], areas,shares,counts,level,[-1,0],ws([-1,0])));
        units = units.concat(tileWorldSide(X1,Y0+rd,  [0,1], Dp-2*rd,rd, [-1,0],areas,shares,counts,level,[1, 0],ws([ 1,0])));
        const cores = [R(CR.x0,CR.y0,coreLen,rd,'core'), R(CR.x1-coreLen,CR.y1-rd,coreLen,rd,'core')];
        const court = R(X0+rd, Y0+rd, W-2*rd, Dp-2*rd, 'court');
        return { units, cores, corrRect:court, corrRects:[], aptD:rd, corr, coreCount:2, double:false,
          frame:{...eff,x0:X0,y0:Y0,x1:X1,y1:Y1}, envelope:frame, pulled:false, level, isSB, devkind, court };
      }
    }

    /* BAR / BLADE */
    const point      = kind.core === 'point';
    const bladeDepth = Math.min(eff.depth, corr + 4.0);
    const bladeVst   = Math.max(0, aptD - (bladeDepth - corr) / 2);
    const coreW      = point ? Math.min(eff.depth, 6.0) : bladeDepth;
    let coreCount    = point ? Math.max(1, Math.round(CR.len/24)) : Math.max(1, Math.ceil(CR.len/45));
    if (devkind === 'point_stair') coreCount = 1;
    const refStart = CR.alongX ? CR.x0 : CR.y0;
    const effStart = eff.alongX ? eff.x0 : eff.y0;
    const cores = [], coreSpots = [];
    for (let i = 0; i < coreCount; i++) {
      const cWorld = refStart + CR.len * (i+1) / (coreCount+1);
      const c = snap((cWorld - effStart) - coreLen/2);
      coreSpots.push([c, c+coreLen]);
      if (point) { const v = (eff.depth-coreW)/2; cores.push(mapRect(eff,c,v,coreLen,coreW,{kind:'core'})); }
      else        cores.push(mapRect(eff,c,bladeVst,coreLen,coreW,{kind:'core'}));
    }
    coreSpots.sort((a,b) => a[0]-b[0]);
    const free = []; let cur = 0;
    for (const [a,b] of coreSpots) { if (a-cur>0.6) free.push([cur,a]); cur=Math.max(cur,b); }
    if (eff.len - cur > 0.6) free.push([cur, eff.len]);

    const rows  = single ? [{v:0,nrm:negV}] : [{v:0,nrm:negV},{v:aptD+corr,nrm:vU}];
    let units = [];
    rows.forEach(row => {
      const wscale = 1 - 0.18 * northness(row.nrm);
      for (const [a,b] of free) {
        const sub = tileRow(b-a, aptD, areas, shares, counts, eff, row.v, level, wscale);
        // world-position shift
        sub.forEach(s => { if (eff.alongX) s.x += a; else s.y += a; s.nrm = row.nrm; });
        // flex-fill to eliminate wasted space (widths finalised here, before host runs rooms)
        flexFill(sub, b-a, eff, aptD, areas);
        // S-wall party wall steps
        applySwalls(sub, aptD, eff, row.nrm);
        units = units.concat(sub);
      }
    });

    const corrRect = mapRect(eff, 0, aptD, eff.len, corr, { kind:'corridor' });
    return { units, cores, corrRect, corrRects:[corrRect], aptD, corr, coreCount, double:!single,
      frame:eff, envelope:frame, pulled, level, isSB, devkind };
  }

  /* ── Top-level entry point ───────────────────────────────────────────────── */
  function generateFloorplan(input) {
    const {
      F = 30, D = 38,
      setbacks = {},
      corridor = 1.8, coreLen = 6.0,
      storeys = 5, f2f = 3.1, dig = 0,
      mix    = { b1:30, b2:50, b3:20 },
      areas  = { b1:50, b2:70, b3:90 },
      devkind = 'double_loaded',
      northVec = [0, 1],
      minD = 6, maxD = 10,
    } = input;

    const sb = setbacks;
    const bx0 = sb.left  ?? sb.side  ?? 3;
    const by0 = sb.front ?? 5.4;
    const bx1 = F - (sb.right ?? sb.side ?? 3);
    const by1 = D - (sb.rear  ?? 4.8);
    const siteArea = F * D;

    if (bx1 - bx0 < 3 || by1 - by0 < 3) {
      return { levels:[], typFrame:null, sbFrame:null, siteArea, counts:{b1:0,b2:0,b3:0}, total:0,
               warnings:['Setbacks exceed site — no buildable envelope.'] };
    }

    const mixTotal = (mix.b1||0)+(mix.b2||0)+(mix.b3||0) || 1;
    const shares   = { b1:(mix.b1||0)/mixTotal, b2:(mix.b2||0)/mixTotal, b3:(mix.b3||0)/mixTotal };
    const counts   = { b1:0, b2:0, b3:0 };

    const typFrame = buildFrame(bx0, by0, bx1, by1);
    const upper = sb.upper ?? 3;
    const ubx0 = bx0+upper, uby0 = by0+upper, ubx1 = bx1-upper, uby1 = by1-upper;
    const haveSB = storeys > 1 && ubx1 > ubx0 && uby1 > uby0;
    const sbFrame = haveSB ? buildFrame(ubx0, uby0, ubx1, uby1) : typFrame;
    const coreRef = haveSB ? sbFrame : typFrame;

    const opts = { corr:corridor, coreLen, devkind, northVec, areas, minD, maxD };
    const levels = [];
    for (let s = 0; s < storeys; s++) {
      const isSB = (s === storeys-1) && haveSB;
      const fr   = isSB ? sbFrame : typFrame;
      const lvl  = layoutLevel(fr, opts, shares, counts, s, isSB, coreRef);
      levels.push(Object.assign(lvl, { z: s*f2f - dig, h: f2f }));
    }

    // recount from placed units
    counts.b1=counts.b2=counts.b3=0;
    levels.forEach(l => l.units.forEach(u => counts[u.type]++));
    const total = counts.b1+counts.b2+counts.b3;

    const warnings = [];
    if (total === 0) warnings.push('No apartments placed — check mix or setbacks.');
    ['b1','b2','b3'].forEach(t => {
      const got = total ? counts[t]/total : 0;
      if (Math.abs(got - shares[t]) > 0.15 && shares[t] > 0)
        warnings.push(`${t.replace('b','')}-bed: ${Math.round(got*100)}% landed vs ${Math.round(shares[t]*100)}% target`);
    });

    return { version:'0.1', levels, typFrame, sbFrame, coreRef, siteArea, counts, total,
             targetMix:shares, warnings };
  }

  return {
    version: '0.1', GRID, DEV_KINDS,
    generateFloorplan, layoutLevel, buildFrame, mapRect, tileRow, flexFill, applySwalls,
  };
});