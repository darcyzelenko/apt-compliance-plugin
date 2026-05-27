const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel,
  BorderStyle, WidthType, ShadingType, VerticalAlign,
  SimpleField, PageBreak, TabStopType, TabStopPosition,
} = require('docx');
const fs = require('fs');

// Read compliance data from stdin
let raw = '';
process.stdin.on('data', d => raw += d);
process.stdin.on('end', () => {
  try {
    const payload = JSON.parse(raw);
    const buf = buildReport(payload);
    buf.then(b => {
      process.stdout.write(b);
    }).catch(e => {
      process.stderr.write('Error: ' + e.message + '\n');
      process.exit(1);
    });
  } catch(e) {
    process.stderr.write('Parse error: ' + e.message + '\n');
    process.exit(1);
  }
});

// ─── Colour palette ───────────────────────────────────────────────────────
const C = {
  INK:    '1A1916',
  DIM:    '6B6860',
  MUT:    'A8A49E',
  ACC:    'E85D25',
  PASS:   '1A7A4A',
  FAIL:   'C0392B',
  WARN:   'B45309',
  BLUE:   '1A3A5F',
  SURF2:  'F0EEEC',
  PBGD:   'EDF7F2',
  FBGD:   'FDF0EE',
  WBGD:   'FEF9F0',
  WHITE:  'FFFFFF',
  NONE:   'auto',
};

// ─── Border helpers ───────────────────────────────────────────────────────
const bd  = (col='CCCCCC', sz=6) => ({ style: BorderStyle.SINGLE, size: sz, color: col });
const bds = (col='CCCCCC', sz=6) => ({ top: bd(col,sz), bottom: bd(col,sz), left: bd(col,sz), right: bd(col,sz) });
const noBorder = { style: BorderStyle.NONE, size: 0, color: C.WHITE };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

// A4 content width in DXA: 11906 - 1800 (left 1.25cm) - 1800 (right) = 8306 → use 8600
const TW = 9200; // table width DXA

// ─── Cell helpers ─────────────────────────────────────────────────────────
function hdrCell(text, w, shade=C.BLUE) {
  return new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: { fill: shade, type: ShadingType.CLEAR },
    borders: bds(C.BLUE, 8),
    margins: { top: 80, bottom: 80, left: 140, right: 140 },
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, color: C.WHITE, size: 18, font: 'Arial' })]
    })]
  });
}

function dataCell(children_or_text, w, shade=null, bold=false, color=C.INK, center=false) {
  const children = typeof children_or_text === 'string'
    ? [new TextRun({ text: children_or_text, bold, color, size: 18, font: 'Arial' })]
    : children_or_text;
  return new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: shade ? { fill: shade, type: ShadingType.CLEAR } : { fill: C.WHITE, type: ShadingType.CLEAR },
    borders: bds('D0CCC6', 4),
    margins: { top: 60, bottom: 60, left: 140, right: 140 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: center ? AlignmentType.CENTER : AlignmentType.LEFT,
      children
    })]
  });
}

function statusCell(pass, w, note='') {
  let text, shade, color;
  if (pass === true)  { text = '✔ COMPLIES';     shade = C.PBGD; color = C.PASS; }
  else if (pass === false) { text = '✖ NON-COMPLIANT'; shade = C.FBGD; color = C.FAIL; }
  else                { text = '⚠ NOTE';          shade = C.WBGD; color = C.WARN; }
  if (note) text += '\n' + note;
  return new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: { fill: shade, type: ShadingType.CLEAR },
    borders: bds('D0CCC6', 4),
    margins: { top: 60, bottom: 60, left: 140, right: 140 },
    verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text, bold: true, color, size: 16, font: 'Arial' })]
    })]
  });
}

function aiCell(text, w) {
  return dataCell(
    [new TextRun({ text: '⚙ AI: ', bold: true, color: C.DIM, size: 16, font: 'Arial' }),
     new TextRun({ text, color: C.INK, size: 16, font: 'Arial', italics: true })],
    w
  );
}

function practCell(text, w) {
  return dataCell(
    [new TextRun({ text: '✎ Practitioner: ', bold: true, color: C.WARN, size: 16, font: 'Arial' }),
     new TextRun({ text, color: C.INK, size: 16, font: 'Arial', italics: true })],
    w
  );
}

function spanningRow(text, shade=C.SURF2) {
  return new TableRow({
    children: [new TableCell({
      columnSpan: 5,
      width: { size: TW, type: WidthType.DXA },
      shading: { fill: shade, type: ShadingType.CLEAR },
      borders: { top: bd(C.ACC, 10), bottom: noBorder, left: noBorder, right: noBorder },
      margins: { top: 80, bottom: 80, left: 140, right: 140 },
      children: [new Paragraph({
        children: [new TextRun({ text, bold: true, color: C.ACC, size: 18, font: 'Arial' })]
      })]
    })]
  });
}

// ─── Heading helpers ──────────────────────────────────────────────────────
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.ACC, space: 4 } },
    children: [new TextRun({ text, bold: true, color: C.BLUE, size: 28, font: 'Arial' })]
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 240, after: 80 },
    children: [new TextRun({ text, bold: true, color: C.BLUE, size: 22, font: 'Arial' })]
  });
}
function p(text, opts={}) {
  return new Paragraph({
    spacing: { after: 80 },
    children: [new TextRun({ text, color: C.DIM, size: 18, font: 'Arial', ...opts })]
  });
}
function gap(sz=160) {
  return new Paragraph({ spacing: { after: sz }, children: [new TextRun('')] });
}
function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// ─── Status icon helper ───────────────────────────────────────────────────
function passIcon(v) {
  if (v === true)  return { text: '✔', color: C.PASS };
  if (v === false) return { text: '✖', color: C.FAIL };
  return { text: '⚠', color: C.WARN };
}

// ─── Main builder ─────────────────────────────────────────────────────────
async function buildReport(payload) {
  const {
    project = {},
    results = {},
    building_results = null,
    jurisdiction = 'VIC',
    generated_at = new Date().toISOString(),
  } = payload;

  const isNSW  = jurisdiction === 'NSW';
  const isBP   = jurisdiction === 'BEST_PRACTICE';
  const isVIC  = jurisdiction === 'VIC';
  const showNSW = isNSW || isBP;
  const showVIC = isVIC || isBP;

  // Apartment-level results (single apt) or building summary
  const aptData = results;
  const bldData = building_results;
  const s = aptData.summary || {};
  const meta = aptData.meta || {};
  const bldSummary = bldData ? bldData.summary || {} : null;
  const apts = bldData ? (bldData.apartments || []) : null;

  // Project info
  const proj = {
    name:      project.name      || '[Project Name]',
    address:   project.address   || '[Full Site Address]',
    applicant: project.applicant || '[Applicant / Owner Name]',
    designer:  project.designer  || '[Architect / Designer Name]',
    certifier: project.certifier || '[Principal Certifier Name & Accreditation No.]',
    ref:       project.ref       || '[Report Reference No.]',
    date:      project.date      || new Date().toLocaleDateString('en-AU'),
    basix:     project.basix     || '[BASIX Certificate No. — NSW only]',
    frv:       project.frv       || '[FRV Report No. — if applicable]',
    perf_sol:  project.perf_sol  || '[Performance Solution Report No. — if applicable]',
    storeys:   project.storeys   || meta.storeys || '[Number]',
    rise:      project.rise      || meta.rise    || '[Number]',
    height:    project.height    || meta.height  || '[metres]',
    gfa:       project.gfa       || meta.gfa     || '[m²]',
    ncc:       'NCC Volume One 2022',
    version:   '1.0',
  };

  // Unit mix summary
  const mix = bldSummary ? bldSummary.apartment_mix : {};
  const totalUnits = bldSummary ? bldSummary.total_units : (meta.bedroom_count >= 0 ? 1 : 0);
  const mixStr = Object.entries(mix).filter(([,v])=>v>0).map(([k,v])=>`${v} × ${k}`).join(', ') || `1 × ${meta.bedroom_count || '?'}BR`;

  // ─── Cover page ──────────────────────────────────────────────────────────
  const coverRows = [
    ['Project', proj.name,       'Report Ref.',  proj.ref],
    ['Address', proj.address,    'Date',         proj.date],
    ['Applicant / Owner', proj.applicant, 'Jurisdiction', jurisdiction === 'BEST_PRACTICE' ? 'Best Practice (NSW + VIC)' : jurisdiction],
    ['Architect / Designer', proj.designer, 'NCC Version', proj.ncc],
    ['Principal Certifier', proj.certifier, 'Version', proj.version],
  ];

  const coverTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [1800, 2800, 1800, 2800],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            columnSpan: 4,
            width: { size: TW, type: WidthType.DXA },
            shading: { fill: C.BLUE, type: ShadingType.CLEAR },
            borders: noBorders,
            margins: { top: 200, bottom: 200, left: 200, right: 200 },
            children: [
              new Paragraph({
                alignment: AlignmentType.LEFT,
                children: [new TextRun({ text: 'DESIGN COMPLIANCE REPORT', bold: true, color: C.WHITE, size: 40, font: 'Arial' })]
              }),
              new Paragraph({
                alignment: AlignmentType.LEFT,
                children: [new TextRun({ text: 'For Building Permit Application & Certification', color: 'AABBCC', size: 22, font: 'Arial' })]
              }),
            ]
          })
        ]
      }),
      ...coverRows.map(([l1, v1, l2, v2]) => new TableRow({
        children: [
          dataCell(l1, 1800, C.SURF2, true, C.INK),
          dataCell(v1, 2800),
          dataCell(l2, 1800, C.SURF2, true, C.INK),
          dataCell(v2, 2800),
        ]
      }))
    ]
  });

  // Legend key
  const legendTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [2300, 2300, 2300, 2300],
    rows: [new TableRow({
      children: [
        dataCell([new TextRun({ text: '⚙  AI Populated / Assessed', color: C.DIM, size: 16, font: 'Arial', bold: true })], 2300, C.SURF2),
        dataCell([new TextRun({ text: '✎  Practitioner to Complete', color: C.WARN, size: 16, font: 'Arial', bold: true })], 2300, C.WBGD),
        dataCell([new TextRun({ text: '✔  Complies', color: C.PASS, size: 16, font: 'Arial', bold: true })], 2300, C.PBGD),
        dataCell([new TextRun({ text: '✖  Non-Compliant / Note', color: C.FAIL, size: 16, font: 'Arial', bold: true })], 2300, C.FBGD),
      ]
    })]
  });

  // ─── Part 1: Compliance Design Parameters ─────────────────────────────
  const part1Rows = [
    ['NCC Version', proj.ncc],
    ['Building Classification', 'Class 2 — Residential'],
    ['Type of Construction', '✎ Type A / B / C — Practitioner to confirm'],
    ['Number of Storeys', String(proj.storeys)],
    ['Rise in Storeys', String(proj.rise)],
    ['Effective Height', proj.height + ' m'],
    ['Gross Floor Area (GFA)', proj.gfa + ' m²'],
    ['Total Dwellings / Unit Mix', `${totalUnits} total  |  ${mixStr}`],
    showNSW ? ['BASIX Certificate No.', proj.basix] : null,
    ['FRV Report & Consent No.', proj.frv],
    ['Performance Solution Ref.', proj.perf_sol],
    ['Applicable Jurisdiction', jurisdiction === 'BEST_PRACTICE' ? 'Best Practice (NSW ADG + VIC ADG)' : (isNSW ? 'NSW — SEPP 65 & ADG 2015' : 'VIC — ADG 2017 · Clause 55 & 58')],
  ].filter(Boolean);

  const part1Table = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [3000, 6200],
    rows: [
      new TableRow({ children: [hdrCell('Parameter', 3000), hdrCell('Value / Reference', 6200)] }),
      ...part1Rows.map(([k, v]) => new TableRow({
        children: [
          dataCell(k, 3000, C.SURF2, true),
          dataCell(v.startsWith('✎') ? v.replace('✎ ', '') : v, 6200,
            v.startsWith('✎') ? C.WBGD : null,
            false,
            v.startsWith('✎') ? C.WARN : C.INK
          ),
        ]
      }))
    ]
  });

  // ─── Part 2: ADG Compliance (per-apt or building-level) ───────────────
  // Build the 5-column detailed table
  // Cols: Ref | Control | Requirement | Proposed / AI Assessment | Status
  const COL = [1100, 2500, 1500, 2600, 1500];

  function adgRow(ref, control, req, proposed, pass) {
    return new TableRow({
      children: [
        dataCell(ref, COL[0], null, true),
        dataCell(control, COL[1]),
        dataCell(req, COL[2], null, false, C.DIM, true),
        aiCell(proposed, COL[3]),
        statusCell(pass, COL[4]),
      ]
    });
  }
  function practRow(ref, control, req, instruction, pass=null) {
    return new TableRow({
      children: [
        dataCell(ref, COL[0], null, true),
        dataCell(control, COL[1]),
        dataCell(req, COL[2], null, false, C.DIM, true),
        practCell(instruction, COL[3]),
        statusCell(pass, COL[4]),
      ]
    });
  }

  // Pull data from apt results
  const bedrooms   = aptData.bedrooms    || [];
  const living     = aptData.living      || [];
  const storage    = aptData.storage     || {};
  const pos        = aptData.pos         || [];
  const vent       = aptData.ventilation || {};
  const access     = aptData.accessibility || {};
  const daylight   = aptData.daylight    || {};
  const energy     = aptData.energy      || {};
  const rDepth     = aptData.room_depth  || [];
  const aptArea    = aptData.apt_area    || {};
  const glassArea  = aptData.glass_area  || {};
  const ceiling    = aptData.ceiling     || {};

  const bedsPass   = s.bedrooms_pass !== false;
  const livingPass = s.living_pass !== false;
  const storPass   = s.storage_pass !== false;
  const posPass    = s.pos_pass !== false;
  const ventPass   = s.ventilation_pass !== false;
  const accPass    = s.accessibility_pass !== false;
  const dlPass     = s.daylight_pass !== false;
  const depthPass  = s.room_depth_pass !== false;
  const enerPass   = s.energy_pass !== false;

  // Solar access building-level
  const solarCheck = bldSummary ? (bldSummary.building_checks||[]).find(c=>c.check_name==='solar_access') : null;
  const cvCheck    = bldSummary ? (bldSummary.building_checks||[]).find(c=>c.check_name==='cross_ventilation') : null;
  const adaptCheck = bldSummary ? (bldSummary.building_checks||[]).find(c=>c.check_name==='adaptable_dwellings') : null;

  // Cross-vent depth
  const cvPaths = vent.cross_ventilation?.valid_paths || [];
  const maxCVDepth = cvPaths.length > 0 ? Math.max(...cvPaths.map(p=>p.path_length_m||0)) : null;
  const cvDepthPass = maxCVDepth === null ? null : maxCVDepth <= 18;

  // Bedroom details
  const bedroomDetail = bedrooms.map(b => `${b.room}: ${b.width_m}w × ${b.depth_m}d m, ${b.area_sqm}m²`).join('; ') || 'No bedroom layers detected';
  const livingDetail  = living.map(l  => `Living: ${l.width_m}m wide, ${l.area_sqm}m²`).join('; ') || 'No living layer detected';
  const storDetail    = storage.total_vol ? `${storage.total_vol}m³ total / ${storage.designated_vol}m³ internal` : 'No storage layers detected';
  const posDetail     = pos.map(p => `${p.space}: ${p.area_sqm}m², min dim ${p.min_dim_m}m`).join('; ') || 'No POS layer detected';
  const ventDetail    = vent.cross_ventilation ? `${vent.cross_ventilation.path_count || 0} cross-vent path(s) found` : 'No ventilation data';
  const accDetail     = access.pass_count !== undefined ? `${access.pass_count}/${access.total} accessibility checks passed` : 'Not assessed';
  const dlDetail      = daylight.rooms ? daylight.rooms.map(r=>`${r.room}: ${r.avg_df}% DF`).join('; ') : 'No daylight data';
  const depthDetail   = rDepth.map(r=>`${r.room}: ${r.depth_used_m}m (max ${r.limit_m}m)`).join('; ') || 'No depth data';
  const cvPassBool    = vent.cross_ventilation?.pass;
  const cvPct         = bldSummary ? Math.round((bldSummary.cross_ventilation_pct||0)*100)+'%' : (cvPassBool ? 'Pass' : 'Fail');

  const adgRows_vic = [
    spanningRow('Part 3 — Siting the Development'),
    adgRow('3B-2', 'Overshadowing of neighbouring properties minimised during midwinter', 'Required', 'Shadow diagram required — annotate 21 June 9am/12pm/3pm on site plan. Add APT_NORTH layer to enable orientation check.', null),
    adgRow('3C-1', 'Transition between private and public domain without compromising safety', 'Required', 'Assessed from ground floor plan — entry design and security provisions to be verified by certifier.', null),
    adgRow('3D-1', 'Communal open space ≥2.5m²/dwelling or 250m² (≥40 dwellings, VIC)', '2.5m²/dwell', 'Draw APT_COMMUNAL_OS layer to enable automated assessment. ✎ Practitioner: confirm communal open space area on site plan.', null),
    adgRow('3F-1', 'Visual privacy separation — habitable rooms & balconies', '6m / 3m', 'Separation distances to be measured from floor plans per elevation. ✎ Practitioner: annotate on plans.', null),
    spanningRow('Part 4 — Designing the Building'),
    adgRow('4A-1', 'Solar access — living rooms & POS ≥ 2hrs on 21 June (9am–3pm)', showVIC ? 'Informational' : '≥70%',
      solarCheck ? `${solarCheck.value_pct} of apartments achieve ≥2h winter sun (${solarCheck.description})` : 'Assessed from window orientation and daylight data — ' + dlDetail,
      solarCheck ? solarCheck.pass : (dlPass ? true : null)
    ),
    adgRow('4B-3', 'Natural cross ventilation', showNSW ? '≥60%' : '≥40%',
      cvCheck ? cvCheck.description : ventDetail,
      cvCheck ? cvCheck.pass : cvPassBool
    ),
    adgRow('4B-3', 'Cross-ventilation depth ≤18m (glass line to glass line)', '≤18m',
      maxCVDepth !== null ? `Max path length measured: ${maxCVDepth}m` : 'No cross-vent paths detected — assess from floor plan measurements',
      cvDepthPass
    ),
    adgRow('4C-1', 'Ceiling height — habitable rooms', showNSW ? '≥2.7m' : 'No hard min.',
      ceiling.assessed ? `${ceiling.ceiling_h_used}m — ${ceiling.overall_pass ? 'complies' : 'BELOW MINIMUM'}` : `${meta.ceiling_h||2.7}m ceiling height set in project parameters`,
      ceiling.assessed ? ceiling.overall_pass : true
    ),
    adgRow('4D-1', 'Apartment minimum internal area', showNSW ? 'Studio 35m² / 1BR 50m² / 2BR 70m² / 3BR 90m²' : 'N/A (VIC)',
      aptArea.assessed ? `${aptArea.total_area_sqm}m² total — req. ${aptArea.required_sqm}m²` : 'VIC does not mandate minimum apartment area — NSW only',
      showNSW ? (aptArea.overall_pass !== false) : true
    ),
    adgRow('4D-1', 'Glazing area ≥10% of habitable room floor area', showNSW ? '≥10%' : 'N/A (VIC)',
      glassArea.assessed && glassArea.rooms && glassArea.rooms.length
        ? glassArea.rooms.map(r=>`${r.room}: ${r.glass_pct}%`).join('; ')
        : (showNSW ? 'Add APT_WINDOW_* layers to calculate — required for NSW' : 'Not required under VIC ADG'),
      showNSW ? (glassArea.overall_pass !== false) : true
    ),
    adgRow('4D-2', 'Habitable room depth ≤ 2.5 × ceiling height', '≤2.5×CH', depthDetail, depthPass),
    adgRow('4D-2', 'Open-plan depth ≤8m (NSW) / ≤9m (VIC) from window', showNSW ? '≤8m' : '≤9m',
      rDepth.filter(r=>r.room_key==='LIVING').map(r=>`Living: ${r.depth_used_m}m / ${r.limit_m}m limit`).join('; ') || 'No living room depth data',
      rDepth.filter(r=>r.room_key==='LIVING').every(r=>r.overall_pass)
    ),
    adgRow('4D-3', 'Bedroom dimensions', 'Main ≥10m² / 3m | Others ≥9m² / 3m', bedroomDetail, bedsPass),
    adgRow('4D-3', 'Living area width', '3.6m (1BR) / 4.0m (2BR+)', livingDetail, livingPass),
    adgRow('4E-1', 'Private open space / balcony', '1BR: 8m²/2m | 2BR: 10m²/2m | 3BR: 12m²/2.4m', posDetail, posPass),
    adgRow('4F-1', 'Circulation core — max apartments per level', showNSW ? '≤8 per core' : 'Informational',
      bldSummary ? `${bldSummary.total_units} apartments total — ✎ Verify core count on plans` : '✎ Verify maximum apartments per core from floor plans',
      null // informational
    ),
    adgRow('4G-1', 'Storage per apartment', showNSW ? '1BR 6m³ / 2BR 8m³ / 3BR 10m³ (50% internal)' : '1BR 10m³ / 2BR 14m³ / 3BR 18m³',
      storDetail, storPass
    ),
    adgRow('4H-1', 'Acoustic privacy — noise transfer minimised', 'Required',
      'Draw APT_NOISE_ROAD / APT_NOISE_RAIL layers to assess influence area. ✎ Acoustic report required where within noise influence zone.',
      null
    ),
    adgRow('4K-1', 'Apartment mix — range of types and sizes', 'Required',
      mixStr || '✎ Confirm unit mix from floor plans',
      null
    ),
    adgRow('4Q-1', 'Adaptable dwellings', showNSW ? '≥50% of total' : '≥50% (BCA D3.3)',
      adaptCheck ? adaptCheck.description : `${access.pass_count||0}/${access.total||8} accessibility checks passed — ${accPass?'meets adaptable criteria':'does not meet adaptable criteria'}`,
      adaptCheck ? adaptCheck.pass : accPass
    ),
  ];

  // NSW-only rows
  const adgRows_nsw_only = showNSW ? [
    spanningRow('NSW-Specific Controls', 'FFF3E0'),
    adgRow('4A-1', 'Solar access — max 15% of apartments with NO direct sunlight', '≤15%',
      solarCheck ? `${Math.round((1-(solarCheck.value||0))*100)}% receive no direct sun` : '✎ Shadow diagram required for NSW solar access compliance',
      solarCheck ? (1 - (solarCheck.value||0)) <= 0.15 : null
    ),
    adgRow('4C-1', 'Ceiling height — non-habitable rooms ≥2.4m', '≥2.4m',
      ceiling.assessed ? `${ceiling.ceiling_h_used}m project ceiling` : '✎ Verify from section drawings',
      ceiling.assessed ? ceiling.overall_pass : null
    ),
    adgRow('4F-1', 'Lifts — max 40 apartments per lift (10+ storeys)', '≤40 per lift',
      `${proj.storeys} storeys — ${parseInt(proj.storeys) >= 10 ? '✎ Verify lift count from plans (10+ storey building)' : 'Not applicable — below 10 storeys'}`,
      parseInt(proj.storeys) >= 10 ? null : true
    ),
    adgRow('4U-1', 'Energy efficiency — BASIX Certificate', 'BASIX required',
      `BASIX Certificate No.: ${proj.basix}. ✎ Confirm targets met in design documentation.`,
      null
    ),
  ] : [];

  const adgTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: COL,
    rows: [
      new TableRow({
        children: COL.map((w, i) => hdrCell(
          ['ADG Ref.', 'Control / Description', 'Requirement', 'Proposed / AI Assessment', 'Status'][i], w
        ))
      }),
      ...adgRows_vic,
      ...adgRows_nsw_only,
    ]
  });

  // VIC ADG design quality principles table
  const DQP = [
    ['1', 'Context and Neighbourhood Character', 'Zone objectives, streetscape character, setbacks and built form reviewed from site analysis documentation'],
    ['2', 'Built Form and Scale',  'Height, massing and articulation reviewed from floor plans, elevations and shadow diagrams'],
    ['3', 'Density',               'FSR and unit yield assessed; separation between dwellings reviewed'],
    ['4', 'Sustainability',         showNSW ? 'BASIX Certificate required — see Appendix' : 'ESD features confirmed; NatHERS or elemental assessment required'],
    ['5', 'Landscape',              'Landscape area, deep soil and communal open space reviewed against ADG minima'],
    ['6', 'Amenity',                'ADG Part 4 quantitative controls assessed — solar, ventilation, ceiling heights, apartment size, storage, balconies'],
    ['7', 'Safety',                 'Access, visibility and surveillance reviewed from ground floor plan and entry design'],
    ['8', 'Housing Diversity & Social Interaction', `Unit mix: ${mixStr}. Adaptable units: ${adaptCheck ? adaptCheck.value_pct : '✎ confirm'}`],
    ['9', 'Aesthetics',             '✎ Assessed from elevation drawings — materiality, articulation and street presentation. Practitioner to confirm.'],
  ];
  const dqpTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [400, 2600, 4200, 2000],
    rows: [
      new TableRow({ children: ['#', 'Principle', 'Response', 'Assessment'].map((t, i) =>
        hdrCell(t, [400, 2600, 4200, 2000][i])) }),
      ...DQP.map(([n, principle, response]) => new TableRow({
        children: [
          dataCell(n, 400, null, true, C.ACC, true),
          dataCell(principle, 2600, null, true),
          aiCell(response, 4200),
          dataCell('⚙ AI ASSESSED', 2000, C.PBGD, false, C.PASS, true),
        ]
      }))
    ]
  });

  // ─── Part 3: NCC Compliance ────────────────────────────────────────────
  const nccRows = [
    ['Part A', 'Building classification', 'Correct for Class 2', 'Extracted from compliance design parameters — Class 2 confirmed', true],
    ['C1', 'Type of Construction', 'Correct for rise/height', '✎ Practitioner to confirm Type A/B/C from compliance parameters table', null],
    ['C2D2', 'Fire compartmentation plan', 'FRLs nominated', '✎ Fire/compartmentation plan to be provided with application — annotate FRLs on all separating elements', null],
    ['C3', 'Protection of openings', 'Rated elements to openings', '✎ Door schedule and fire matrix required — confirm all openings in fire-resisting construction protected', null],
    ['D1', 'Means of escape', 'Travel distances & exit widths', '✎ Measure travel distances from floor plans — annotate on drawings per NCC requirements', null],
    ['D2 / D4', 'Access & egress / Disability access', 'AS 1428.1 compliant', `Accessibility checks: ${access.pass_count||'?'}/${access.total||8} passed — ${accPass?'Complies':'Review required'}`, accPass],
    ['E1', 'Fire fighting equipment', 'Hydrants, hose reels, sprinklers', '✎ Fire services plan required — confirm hydrants, hose reels, extinguishers per NCC', null],
    ['E2', 'Smoke hazard management', 'Detection & management system', '✎ Mechanical and fire services drawings required — smoke detection, alarm and management system', null],
    ['E3', 'Lift installations', 'Emergency lift if required', '✎ Lift schedule and section drawings required — confirm emergency lift and fire service controls if applicable', null],
    ['E4', 'Emergency lighting & exit signs', 'AS/NZS 2293.1', '✎ Confirmed from reflected ceiling plans and electrical drawings', null],
    ['F1', 'Damp & weatherproofing', 'AS 4654.2 (ext) / AS 3740 (wet areas)', '✎ Waterproofing schedule required — external areas AS 4654.2, wet areas AS 3740', null],
    ['F3', 'Room dimensions', 'Min ceiling heights & areas',
      `Ceiling: ${meta.ceiling_h||2.7}m | Bedrooms: ${bedroomDetail.slice(0,80)} | Living: ${livingDetail.slice(0,60)}`,
      bedsPass && livingPass
    ],
    ['F4', 'Light & ventilation', 'Natural light ≥10% floor area',
      glassArea.rooms && glassArea.rooms.length
        ? glassArea.rooms.map(r=>`${r.room}: ${r.glass_pct}%`).join('; ')
        : '✎ Window schedule required — confirm 10% glass area per habitable room',
      glassArea.assessed ? glassArea.overall_pass : null
    ],
    ['F5', 'Sound transmission', 'Acoustic ratings nominated', '✎ Wall/floor schedule with acoustic ratings required — confirm compliance with NCC F5', null],
    ['Section J', 'Energy efficiency', showNSW ? 'BASIX Certificate' : 'NatHERS or elemental',
      showNSW ? `BASIX No.: ${proj.basix} — see Appendix` : '✎ NatHERS certificate or elemental assessment required',
      null
    ],
    ['Structural', 'S.238 Certificate of Compliance', 'Registered structural engineer', '✎ Engineer to sign and attach Section 238 Certificate with permit application', null],
    ['Balustrades', 'Structural design to AS/NZS 1170.1', 'Heights & climbability per NCC', '✎ Structural drawings required — balustrade loading, heights, connection details', null],
    ['Services', 'Penetrations in fire-rated construction', 'AS 4072.1 fire stopping', '✎ Services penetrations schedule and fire matrix required', null],
    ['Waterproofing', 'Schedule per VBA Practice Guide 1.3.6', 'AS 3740 / AS 4654.2', '✎ Waterproofing schedule required with membrane specs, substrates and coverage areas', null],
  ];

  const nccTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [1200, 2400, 1600, 2800, 1200],
    rows: [
      new TableRow({ children: ['NCC Ref.', 'Compliance Requirement', 'Standard', 'Assessment / Documentation', 'Status'].map((t, i) =>
        hdrCell(t, [1200, 2400, 1600, 2800, 1200][i])) }),
      ...nccRows.map(([ref, req, std, assess, pass]) => new TableRow({
        children: [
          dataCell(ref, 1200, null, true),
          dataCell(req, 2400),
          dataCell(std, 1600, null, false, C.DIM),
          assess.startsWith('✎') ? practCell(assess.replace('✎ ',''), 2800) : aiCell(assess, 2800),
          statusCell(pass, 1200),
        ]
      }))
    ]
  });

  // ─── Part 4: Documentation Checklist ──────────────────────────────────
  const checklistItems = [
    ['Building Permit Application (Form 1)', '✎ Applicant', 'Required — lodge with RBS'],
    ['Working Drawings — Architectural (floor plans, elevations, sections, RCPs, cover page with compliance parameters)', '⚙ AI assessed', 'Required'],
    ['Working Drawings — Structural (concept, design, balustrades, loads, penetrations)', '✎ Structural Engineer', 'Required'],
    ['Site Survey — AHD/RL by Licensed Surveyor', '✎ Surveyor', 'Required'],
    ['Compliance Design Parameters Table (per Appendix A, VBA Practice Guide)', '⚙ AI populated', 'Required — see Part 1 of this report'],
    ['Wall Type Schedule (inc. acoustic and FRL ratings)', '✎ Architect / Designer', 'Required'],
    ['Floor / Roof / Ceiling Type Schedule', '✎ Architect / Designer', 'Required'],
    ['Door & Window Schedule (inc. fire protection)', '✎ Architect / Designer', 'Required'],
    ['Waterproofing Schedule (AS 3740 / AS 4654.2)', '✎ Architect / Designer', 'Required'],
    ['Services Penetrations Schedule (AS 4072.1)', '✎ Services Engineers', 'Required'],
    ['Warning Signage Schedule', '✎ Architect / Designer', 'Required'],
    ['Specifications (NATSPEC Class 2 or equivalent)', '⚙ AI / ✎ Practitioner', 'Required'],
    ['Section 238 Certificate of Compliance — Structural', '✎ Structural Engineer', 'Required'],
    ['Fire Engineering Report / Performance Solution', '✎ Fire Engineer', 'If applicable'],
    showVIC ? ['FRV Report and Consent (VIC)', '✎ Applicant', `FRV Ref: ${proj.frv}`] : null,
    showNSW ? ['BASIX Certificate', '✎ Energy Assessor', `Certificate No.: ${proj.basix}`] : null,
    showVIC ? ['NatHERS Certificate or Elemental Assessment (VIC)', '✎ Energy Assessor', 'Required'] : null,
    ['Acoustic Report', '✎ Acoustic Consultant', 'If required by Council or NCC'],
    showNSW ? ['SEPP 65 ADG Compliance Table', '⚙ AI populated', 'Required — see Part 2 of this report'] : null,
    ['Shadow Diagrams — 21 June (9am, 12pm, 3pm)', '⚙ AI assessed / ✎ Architect', 'Required for solar access assessment'],
    ['Landscape Plan (inc. deep soil zones, communal open space)', '✎ Landscape Architect', 'Required'],
  ].filter(Boolean);

  const checklistTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [5200, 1900, 2100],
    rows: [
      new TableRow({ children: hdrCell('Document / Requirement', 5200) && ['Document / Requirement', 'Responsibility', 'Status'].map((t, i) =>
        hdrCell(t, [5200, 1900, 2100][i])) }),
      ...checklistItems.map(([doc, who, status]) => new TableRow({
        children: [
          dataCell(doc, 5200),
          dataCell(who, 1900, who.startsWith('✎') ? C.WBGD : null, false,
            who.startsWith('✎') ? C.WARN : C.DIM),
          dataCell(status, 2100, status === 'Required' ? C.PBGD : null, false,
            status === 'Required' ? C.PASS : C.DIM),
        ]
      }))
    ]
  });

  // ─── Appendix A: Per-Apartment Summary ────────────────────────────────
  const aptSummaryRows = apts ? apts.map(apt => {
    const as = apt.summary || {};
    const checks = [
      ['Bedrooms', as.bedrooms_pass],
      ['Living', as.living_pass],
      ['Room Depth', as.room_depth_pass],
      ['Storage', as.storage_pass],
      ['POS', as.pos_pass],
      ['Ventilation', as.ventilation_pass],
      ['Accessibility', as.accessibility_pass],
      ['Daylight', as.daylight_pass],
      ['Energy', as.energy_pass],
    ];
    const failures = checks.filter(([,v])=>v===false).map(([k])=>k).join(', ');
    return new TableRow({
      children: [
        dataCell(apt.unit_id, 700, null, true),
        dataCell(apt.apartment_type || '?', 1200),
        statusCell(apt.pass, 1200),
        dataCell(apt.has_cross_ventilation ? '✔' : '✖', 1000, null, false,
          apt.has_cross_ventilation ? C.PASS : C.FAIL, true),
        dataCell(apt.is_adaptable ? '✔' : '✖', 1000, null, false,
          apt.is_adaptable ? C.PASS : C.FAIL, true),
        dataCell(failures || (apt.pass ? 'All checks passed' : '✖ Check required'), 4100, null, false,
          failures ? C.FAIL : C.PASS),
      ]
    });
  }) : [];

  const aptTable = apts && apts.length > 0 ? new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [700, 1200, 1200, 1000, 1000, 4100],
    rows: [
      new TableRow({
        children: ['Unit', 'Type', 'Overall', 'Cross Vent.', 'Adaptable', 'Notes / Failures'].map((t, i) =>
          hdrCell(t, [700,1200,1200,1000,1000,4100][i]))
      }),
      ...aptSummaryRows,
    ]
  }) : null;

  // ─── Signatures page ──────────────────────────────────────────────────
  const sigTable = new Table({
    width: { size: TW, type: WidthType.DXA },
    columnWidths: [4600, 4600],
    rows: [
      new TableRow({ children: [hdrCell('Prepared by — Architect / Designer', 4600), hdrCell('Certified by — Principal Certifier', 4600)] }),
      new TableRow({ children: [
        new TableCell({
          width: { size: 4600, type: WidthType.DXA },
          borders: bds('D0CCC6', 4),
          margins: { top: 120, bottom: 360, left: 160, right: 160 },
          children: [
            p(`Name: ${proj.designer}`, { size: 18 }),
            p('Practice / Firm: ___________________________'),
            p('Registration No.: ___________________________'),
            p('Signature: ___________________________'),
            p(`Date: ${proj.date}`),
            gap(80),
            p('✎ By signing this report, the architect / designer confirms that the information provided is accurate and complete to the best of their knowledge.', { size: 16, italics: true, color: C.WARN }),
          ]
        }),
        new TableCell({
          width: { size: 4600, type: WidthType.DXA },
          borders: bds('D0CCC6', 4),
          margins: { top: 120, bottom: 360, left: 160, right: 160 },
          children: [
            p(`Name: ${proj.certifier}`),
            p('Accreditation No.: ___________________________'),
            p('Signature: ___________________________'),
            p('Date of Review: ___________________________'),
            p('Building Permit No.: ___________________________'),
            gap(80),
            p('✎ The Principal Certifier confirms this report has been reviewed and is accepted as part of the building permit application documentation.', { size: 16, italics: true, color: C.WARN }),
          ]
        }),
      ]})
    ]
  });

  // ─── Assemble document ────────────────────────────────────────────────
  const sections_content = [
    // Cover
    coverTable,
    gap(120),
    legendTable,
    pageBreak(),

    // Part 1
    h1('Part 1 — Compliance Design Parameters'),
    p('These parameters must appear on the drawing cover sheet per Appendix A of the VBA Design Documentation Practice Guide (Class 2). All values to be verified by the Principal Certifier against submitted drawings.'),
    gap(80),
    part1Table,
    pageBreak(),

    // Part 2
    h1('Part 2 — ADG Compliance Table'),
    p(isNSW
      ? 'Prepared in accordance with SEPP 65 — Design Quality of Residential Apartment Development and the Apartment Design Guide (ADG) 2015. Each criterion references the ADG control number.'
      : isBP
      ? 'Assessed against VIC ADG 2017 (Cl. 55 & 58) and NSW ADG 2015 best-practice targets. Items marked ⚙ are AI-assessed from DXF data; items marked ✎ require practitioner input.'
      : 'Prepared in accordance with the Victorian Apartment Design Guidelines 2017 (Cl. 55 & 58). Items marked ⚙ are AI-assessed from DXF data; items marked ✎ require practitioner input.'),
    gap(80),

    h2('2.1  Design Quality Principles — Summary'),
    dqpTable,
    gap(120),

    h2('2.2  ADG Quantitative Controls — Detailed Assessment'),
    adgTable,
    pageBreak(),

    // Part 3
    h1('Part 3 — NCC & Building Regulations Compliance'),
    p(`Assessed against the National Construction Code (${proj.ncc}), Building Regulations 2018 (Vic) / EP&A Regulation (NSW), and the VBA Design Documentation Practice Guide for Class 2 residential buildings.`),
    gap(80),
    nccTable,
    pageBreak(),

    // Part 4
    h1('Part 4 — Supporting Documentation Checklist'),
    p('All documentation listed must be provided with the building permit application. Items marked ⚙ are assessed or generated by the platform. Items marked ✎ must be completed by the relevant practitioner.'),
    gap(80),
    checklistTable,
    pageBreak(),

    // Signatures
    h1('Certifier Sign-Off'),
    sigTable,
    pageBreak(),

    // Appendix A — per-apt summary (if building)
    ...(aptTable ? [
      h1('Appendix A — Per-Apartment Compliance Summary'),
      p(`Summary of compliance assessment for each of the ${totalUnits} apartments in the building. Detailed room-by-room analysis available in the Apt. Compliance platform.`),
      gap(80),
      aptTable,
      pageBreak(),
    ] : []),

    // Appendix B — notes / placeholders
    h1('Appendix B — Assessment Notes & Limitations'),
    h2('What this report covers'),
    p('This report is generated automatically from DXF geometry uploaded to the Apt. Compliance platform. It covers room dimensions, storage volumes, private open space, natural ventilation paths, daylight factors, and accessibility proxy checks derived from plan geometry.'),
    gap(80),
    h2('What requires practitioner completion'),
    ...[
      'Fire resistance levels (FRLs) and compartmentation — requires fire engineering assessment',
      'Structural adequacy — requires Section 238 Certificate from a registered structural engineer',
      'Shadow diagrams (solar access) — requires 21 June sun angle simulation on site plans',
      'Acoustic performance — requires wall/floor schedule with acoustic ratings and (where applicable) an acoustic consultant\'s report',
      'Site-specific planning controls — setbacks, FSR, heritage, flooding, bushfire — require site survey and planning permit documentation',
      'Services documentation — hydraulic, electrical, mechanical, fire services plans and schedules',
      'Energy efficiency — NatHERS or BASIX certification required from an accredited energy assessor',
      'Aesthetics (DQP 9) — elevation drawing review required by architect / designer',
      'Waterproofing system selection — manufacturer specifications and test certificates',
      'Signage schedule — exit, directional and fire safety signage to be scheduled by designer',
    ].map(item => new Paragraph({
      numbering: { reference: 'bullets', level: 0 },
      spacing: { after: 60 },
      children: [new TextRun({ text: item, color: C.DIM, size: 18, font: 'Arial' })]
    })),
    gap(120),
    h2('Disclaimer'),
    p('This report is generated from plan geometry and is an informative tool to assist design practitioners and certifiers. It does not constitute a compliance certificate and does not replace the professional judgement of a registered architect, building designer, or building surveyor. All AI-assessed items must be verified by the Principal Certifier against submitted drawings before a building permit is issued.'),
  ];

  const doc = new Document({
    numbering: {
      config: [{
        reference: 'bullets',
        levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }]
      }]
    },
    styles: {
      default: { document: { run: { font: 'Arial', size: 20 } } },
      paragraphStyles: [
        { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 28, bold: true, font: 'Arial', color: C.BLUE },
          paragraph: { spacing: { before: 360, after: 120 }, outlineLevel: 0,
            border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.ACC, space: 4 } } } },
        { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 22, bold: true, font: 'Arial', color: C.BLUE },
          paragraph: { spacing: { before: 240, after: 80 }, outlineLevel: 1 } },
      ]
    },
    sections: [{
      properties: {
        page: {
          size: { width: 11906, height: 16838 }, // A4
          margin: { top: 1000, right: 900, bottom: 1000, left: 900 }
        }
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.ACC, space: 4 } },
            spacing: { after: 80 },
            children: [
              new TextRun({ text: 'DESIGN COMPLIANCE REPORT  |  ', bold: true, color: C.BLUE, size: 16, font: 'Arial' }),
              new TextRun({ text: `${proj.name}  |  `, color: C.DIM, size: 16, font: 'Arial' }),
              new TextRun({ text: proj.ref, color: C.DIM, size: 16, font: 'Arial', italics: true }),
            ]
          })]
        })
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.MUT, space: 4 } },
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: 'Apt. Compliance Platform  |  ', color: C.MUT, size: 14, font: 'Arial' }),
              new TextRun({ text: `Generated ${new Date().toLocaleDateString('en-AU')}`, color: C.MUT, size: 14, font: 'Arial' }),
              new SimpleField('PAGE', undefined, { color: C.MUT, size: 14, font: 'Arial' }),
            ]
          })]
        })
      },
      children: sections_content,
    }]
  });

  return Packer.toBuffer(doc);
}

module.exports = { buildReport };
