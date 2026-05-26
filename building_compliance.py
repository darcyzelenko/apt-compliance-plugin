"""
building_compliance.py
======================
Multi-apartment and building-level compliance for Apt. v0.4

Integrates with the existing compliance_engine.run_compliance() without
modifying it. Drop this file alongside compliance_engine.py.

Layer convention (new, additive):
  APT_01_ROOM_MAINBED, APT_01_ROOM_LIVING, APT_01_STORAGE_DESIGNATED …
  APT_02_ROOM_MAINBED, APT_02_POS_01 …

Files without a unit prefix (APT_ROOM_MAINBED) continue to work as
single-apartment checks — fully backward compatible.

Jurisdiction keys match compliance_engine.py exactly:
  'VIC'            Victorian ADG 2017
  'NSW'            NSW ADG 2015
  'BEST_PRACTICE'  NSW base + quality targets

Building-level thresholds (not encoded in the per-apartment engine):
  VIC:  cross-ventilation ≥ 40 % of apartments (ADG Vic 2017 s.55)
        adaptable dwellings ≥ 50 % (BCA D3.3)
  NSW:  cross-ventilation ≥ 60 % of apartments (NSW ADG 2015 p.88)
        adaptable dwellings ≥ 50 % (BCA D3.3)

Public API
----------
  is_multi_apartment(dxf_text)          -> bool
  detect_unit_ids(dxf_text)             -> List[str]
  extract_unit_dxf(dxf_text, unit_id)   -> str   (normalised, ready for engine)
  check_building(dxf_text, jurisdiction, ceiling_h, compliance_engine_fn)
                                         -> BuildingResult
  building_result_to_dict(result)        -> dict  (JSON-serialisable)
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------------------
# Building-level thresholds (per-apartment thresholds live in compliance_engine)
# ---------------------------------------------------------------------------

BUILDING_THRESHOLDS = {
    'VIC': {
        # ADG Vic 2017 Standards D27/B50
        'cross_ventilation_pct': 0.40,
        # BCA D3.3 / Standard D17
        'adaptable_dwellings_pct': 0.50,
        # Standard B36/D7: 40+ dwellings -> 2.5m2/dwelling or 250m2, lesser
        'communal_open_space_m2_per_dwelling': 2.5,
        'communal_open_space_min_m2': 250.0,
        'communal_open_space_threshold_dwellings': 40,
        # NSW only (not VIC hard rule)
        'max_apts_per_core': None,
        'max_apts_per_lift': None,
        'min_storeys_for_lift_check': None,
        'solar_access_pct': None,
        'solar_hours_min': None,
        'acoustic_separation_m': 3.0,
        'facade_glazing_min_pct': None,
    },
    'NSW': {
        # NSW ADG 2015 4B p.84
        'cross_ventilation_pct': 0.60,
        'adaptable_dwellings_pct': 0.50,
        # NSW ADG 4F p.98: max 8 apartments off circulation core per level
        'max_apts_per_core': 8,
        # NSW ADG 4F: 10+ storeys max 40 apartments per lift
        'max_apts_per_lift': 40,
        # NSW ADG 4A p.80: >=70% receive >=2h direct sun (Sydney/Newcastle/Wollongong)
        # elsewhere NSW >=3h. Using 2h as default.
        'solar_access_pct': 0.70,
        'solar_hours_min': 2,
        'max_no_sun_pct': 0.15,
        # NSW ADG 4G: studio 4m3, 1bed 6m3, 2bed 8m3, 3bed+ 10m3
        'storage_m3': {'studio': 4, '1bed': 6, '2bed': 8, '3bed+': 10},
        'storage_internal_pct': 0.50,
        'communal_open_space_m2_per_dwelling': None,
        'communal_open_space_min_m2': None,
        'communal_open_space_threshold_dwellings': None,
        # Acoustic separation (NSW ADG 4H)
        'acoustic_separation_m': 3.0,
        # Facade glazing: minimum window-to-external-wall-area ratio
        'facade_glazing_min_pct': None,
        # Max apts per lift (NSW ADG 4F: 10+ storeys)
        'max_apts_per_lift': 40,
        'min_storeys_for_lift_check': 10,
    },
    'BEST_PRACTICE': {
        'cross_ventilation_pct': 0.60,
        'adaptable_dwellings_pct': 0.50,
        'max_apts_per_core': 8,
        'max_apts_per_lift': 40,
        'solar_access_pct': 0.70,
        'solar_hours_min': 2,
        'max_no_sun_pct': 0.15,
        'storage_m3': {'studio': 4, '1bed': 6, '2bed': 8, '3bed+': 10},
        'storage_internal_pct': 0.50,
        'communal_open_space_m2_per_dwelling': 2.5,
        'communal_open_space_min_m2': 250.0,
        'communal_open_space_threshold_dwellings': 40,
    },
}


# ---------------------------------------------------------------------------
# Layer prefix regex
# ---------------------------------------------------------------------------

# Matches:  APT_01_ROOM_MAINBED   APT_B3_POS   APT_2_WINDOW_LIVING
# Groups:   (unit_id)  (rest starting from layer type)
UNIT_PREFIX_RE = re.compile(
    r'^(APT_)([A-Za-z0-9]+)_((?:ROOM|STORAGE|POS|WINDOW|DOOR|NORTH|NOISE|WALL|COLUMN|'
    r'OVERHANG|SHAFT|KITCHEN|BATHROOM|FURNITURE).*)$'
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UnitResult:
    unit_id: str
    jurisdiction: str
    pass_overall: bool          # results['summary']['all_pass']
    bedroom_count: int          # results['meta']['bedroom_count']
    apartment_type: str         # 'studio' | '1bed' | '2bed' | '3bed+'
    has_cross_ventilation: bool # results['ventilation']['cross_ventilation']['pass']
    is_adaptable: bool          # results['accessibility']['overall_pass']
    has_north_living: bool      # results['energy']['living_north']
    summary: Dict[str, Any]     # full results['summary'] dict
    checks: Dict[str, Any]      # full results dict minus heavy geometry fields


@dataclass
class BuildingCheckResult:
    check_name: str
    pass_: bool
    value: float        # actual proportion (0–1)
    required: float     # required threshold (0–1)
    description: str


@dataclass
class BuildingSummary:
    jurisdiction: str
    total_units: int
    passing_units: int
    pass_rate: float
    building_checks: List[BuildingCheckResult]
    apartment_mix: Dict[str, int]
    cross_ventilation_pct: float
    adaptable_dwellings_pct: float
    north_living_pct: float
    solar_access_pct: float          # pct apartments with >= solar_hours_min sun
    worst_unit: Optional[str]
    errors: List[str] = field(default_factory=list)


@dataclass
class BuildingResult:
    apartments: List[UnitResult]
    summary: BuildingSummary


# ---------------------------------------------------------------------------
# DXF layer handling
# ---------------------------------------------------------------------------

def is_multi_apartment(dxf_text: str) -> bool:
    """True if the DXF contains at least one unit-prefixed APT layer."""
    for line in dxf_text.splitlines():
        if UNIT_PREFIX_RE.match(line.strip()):
            return True
    return False


def detect_unit_ids(dxf_text: str) -> List[str]:
    """Return sorted list of unit IDs found in the DXF."""
    ids = set()
    for line in dxf_text.splitlines():
        m = UNIT_PREFIX_RE.match(line.strip())
        if m:
            ids.add(m.group(2))
    return sorted(ids)


def extract_unit_dxf(dxf_text, unit_id):
    """
    Extract one unit's entities from a merged multi-apartment DXF.

    Walks entity blocks. For each entity, reads its layer name (group code 8).
    - Layer belongs to this unit (APT_01_*) -> include, normalise name
    - Layer belongs to another unit (APT_02_*) -> skip entire entity block
    - Layer is untagged -> include unchanged
    """
    layer_types = (
        'ROOM', 'STORAGE', 'POS', 'WINDOW', 'DOOR', 'NORTH', 'NOISE',
        'WALL', 'COLUMN', 'OVERHANG', 'SHAFT', 'KITCHEN', 'BATHROOM', 'FURNITURE',
    )
    unit_prefix = 'APT_' + unit_id + '_'

    def classify_layer(lname):
        # Returns 'mine', 'other', or 'untagged'
        if not lname.startswith('APT_'):
            return 'untagged'
        rest = lname[4:]
        parts = rest.split('_', 1)
        if len(parts) < 2:
            return 'untagged'
        cid, lrest = parts[0], parts[1]
        if not any(lrest.startswith(lt) for lt in layer_types):
            return 'untagged'
        return 'mine' if cid == unit_id else 'other'

    lines = dxf_text.splitlines()
    n = len(lines)
    out = []
    i = 0

    while i < n:
        stripped = lines[i].strip()

        # Entity boundary
        if stripped == '0' and i + 1 < n:
            etype = lines[i + 1].strip()
            if etype in ('ENDSEC', 'EOF', 'SECTION', 'HEADER',
                         'CLASSES', 'TABLES', 'BLOCKS', 'OBJECTS', 'THUMBNAILIMAGE'):
                out.append(lines[i])
                i += 1
                continue

            # Collect full entity block until next group-code-0 line
            block = []
            j = i
            while j < n:
                block.append(lines[j])
                j += 1
                if j < n and lines[j].strip() == '0':
                    break

            # Find layer name in block (group code 8 followed by layer name)
            layer_name = None
            layer_idx = None
            for k in range(len(block) - 1):
                if block[k].strip() == '8':
                    layer_name = block[k + 1].strip()
                    layer_idx = k + 1
                    break

            if layer_name is not None:
                cls = classify_layer(layer_name)
                if cls == 'other':
                    # Skip entire entity block
                    i = j
                    continue
                if cls == 'mine':
                    # Normalise: APT_01_ROOM_MAINBED -> APT_ROOM_MAINBED
                    block[layer_idx] = 'APT_' + layer_name[len(unit_prefix):]

            out.extend(block)
            i = j
            continue

        out.append(lines[i])
        i += 1

    return '\n'.join(out)



# ---------------------------------------------------------------------------
# Apartment type
# ---------------------------------------------------------------------------

def _apartment_type(bedroom_count: int) -> str:
    if bedroom_count == 0:
        return 'studio'
    if bedroom_count == 1:
        return '1bed'
    if bedroom_count == 2:
        return '2bed'
    return '3bed+'


# ---------------------------------------------------------------------------
# Summary failure count (used to find worst unit)
# ---------------------------------------------------------------------------

def _summary_failures(unit: UnitResult) -> int:
    """Count False values in results['summary'] pass flags."""
    return sum(
        1 for k, v in unit.summary.items()
        if k.endswith('_pass') and v is False
    )


# ---------------------------------------------------------------------------
# Building-level aggregation
# ---------------------------------------------------------------------------

def aggregate_building(apt_results, jurisdiction='VIC', storey_count=0):
    """Aggregate building-level compliance across all apartment results."""
    jur = jurisdiction.upper()
    thresholds = BUILDING_THRESHOLDS.get(jur, BUILDING_THRESHOLDS['VIC'])
    n = len(apt_results)
    errors = []

    if n == 0:
        errors.append('No apartment results to aggregate.')
        return BuildingSummary(
            jurisdiction=jurisdiction, total_units=0, passing_units=0,
            pass_rate=0.0, building_checks=[], apartment_mix={},
            cross_ventilation_pct=0.0, adaptable_dwellings_pct=0.0,
            north_living_pct=0.0, solar_access_pct=0.0,
            worst_unit=None, errors=errors,
        )

    passing_units   = sum(1 for r in apt_results if r.pass_overall)
    cv_units        = sum(1 for r in apt_results if r.has_cross_ventilation)
    adaptable_units = sum(1 for r in apt_results if r.is_adaptable)
    north_units     = sum(1 for r in apt_results if r.has_north_living)

    # Solar access: only compute when jurisdiction has a solar threshold
    solar_hours_req = thresholds.get('solar_hours_min') or 2
    solar_req_pct   = thresholds.get('solar_access_pct')
    solar_units = 0
    if solar_req_pct is not None:
        for r in apt_results:
            daylight = r.checks.get('daylight', {}) if isinstance(r.checks, dict) else {}
            rooms = daylight.get('rooms', [])
            pos   = daylight.get('pos', [])
            living_sun = any(
                isinstance(rm.get('sun_hours'), (int, float)) and rm['sun_hours'] >= solar_hours_req
                for rm in rooms
                if rm.get('room_key', '').upper() in ('LIVING', 'MAINBED')
            )
            pos_sun = any(
                isinstance(p.get('sun_hours'), (int, float)) and p['sun_hours'] >= solar_hours_req
                for p in pos
            )
            if living_sun or pos_sun:
                solar_units += 1

    cv_pct       = cv_units / n
    adaptable_pct = adaptable_units / n
    north_pct    = north_units / n
    solar_pct    = solar_units / n
    pass_rate    = passing_units / n

    mix = {'studio': 0, '1bed': 0, '2bed': 0, '3bed+': 0}
    for r in apt_results:
        mix[r.apartment_type] += 1

    building_checks = []

    # 1. Cross ventilation
    cv_req = thresholds['cross_ventilation_pct']
    building_checks.append(BuildingCheckResult(
        check_name='cross_ventilation',
        pass_=(cv_pct >= cv_req),
        value=cv_pct,
        required=cv_req,
        description='%d of %d apartments cross ventilated (%d%%) -- %s requires %d%%' % (
            cv_units, n, int(cv_pct*100), jur, int(cv_req*100)
        ),
    ))

    # 2. Adaptable dwellings
    ad_req = thresholds['adaptable_dwellings_pct']
    building_checks.append(BuildingCheckResult(
        check_name='adaptable_dwellings',
        pass_=(adaptable_pct >= ad_req),
        value=adaptable_pct,
        required=ad_req,
        description='%d of %d apartments meet adaptability checks (%d%%) -- BCA D3.3 requires %d%%' % (
            adaptable_units, n, int(adaptable_pct*100), int(ad_req*100)
        ),
    ))

    # 3. North-facing living (informational for VIC, no hard threshold)
    building_checks.append(BuildingCheckResult(
        check_name='north_facing_living',
        pass_=True,
        value=north_pct,
        required=0.0,
        description='%d of %d apartments have north-facing living (%d%%)' % (
            north_units, n, int(north_pct*100)
        ),
    ))

    # 4. Solar access (NSW/BEST_PRACTICE: >=70% of apartments get >=2h sun)
    if solar_req_pct is not None:
        no_sun_pct = 1.0 - solar_pct
        max_no_sun = thresholds.get('max_no_sun_pct', 0.15)
        building_checks.append(BuildingCheckResult(
            check_name='solar_access',
            pass_=(solar_pct >= solar_req_pct and no_sun_pct <= max_no_sun),
            value=solar_pct,
            required=solar_req_pct,
            description='%d of %d apartments receive >=%dh winter sun (%d%%) -- %s requires %d%%' % (
                solar_units, n, solar_hours_req, int(solar_pct*100), jur, int(solar_req_pct*100)
            ),
        ))

    # 5. NSW: apartments per core (informational -- requires core geometry)
    max_per_core = thresholds.get('max_apts_per_core')
    if max_per_core is not None:
        building_checks.append(BuildingCheckResult(
            check_name='apartments_per_core',
            pass_=True,   # informational -- cannot compute without core geometry
            value=0.0,
            required=float(max_per_core),
            description='NSW ADG 4F: max %d apartments per circulation core per level -- verify on plan' % max_per_core,
        ))

    # 6. NSW storage aggregate (>=50% of storage must be within apartment)
    storage_m3 = thresholds.get('storage_m3')
    if storage_m3 is not None:
        storage_internal_req = thresholds.get('storage_internal_pct', 0.50)
        # Check each apartment against NSW storage minimums
        storage_pass_count = 0
        for r in apt_results:
            st = r.checks.get('storage', {}) if isinstance(r.checks, dict) else {}
            req = storage_m3.get(r.apartment_type, 6)
            provided = st.get('total_vol', 0) or 0
            if provided >= req:
                storage_pass_count += 1
        storage_apt_pct = storage_pass_count / n
        building_checks.append(BuildingCheckResult(
            check_name='storage_nsw',
            pass_=(storage_apt_pct >= 1.0),
            value=storage_apt_pct,
            required=1.0,
            description='NSW 4G: %d of %d apartments meet NSW storage minimums (%d%%)' % (
                storage_pass_count, n, int(storage_apt_pct*100)
            ),
        ))

    # 7. VIC communal open space (40+ dwellings)
    cos_threshold = thresholds.get('communal_open_space_threshold_dwellings')
    if cos_threshold is not None and n >= cos_threshold:
        cos_per_dwell = thresholds.get('communal_open_space_m2_per_dwelling', 2.5)
        cos_min = thresholds.get('communal_open_space_min_m2', 250.0)
        cos_req = min(cos_per_dwell * n, cos_min)
        building_checks.append(BuildingCheckResult(
            check_name='communal_open_space',
            pass_=True,   # informational -- requires site geometry
            value=0.0,
            required=cos_req,
            description='VIC D7: %d+ dwellings require %.0fm2 communal open space -- verify on site plan' % (
                cos_threshold, cos_req
            ),
        ))

    # 8. Acoustic separation (VIC + NSW ADG 4H: ≥3m from noise sources to bedrooms)
    acoustic_m = thresholds.get('acoustic_separation_m', 3.0)
    if acoustic_m is not None:
        # Count apartments with noise sources in range
        acoustic_fail_count = 0
        for r in apt_results:
            noise = r.checks.get('noise', {}) if isinstance(r.checks, dict) else {}
            if noise.get('assessed') and noise.get('in_range'):
                acoustic_fail_count += 1
        acoustic_pass = acoustic_fail_count == 0
        if any((r.checks.get('noise', {}) if isinstance(r.checks, dict) else {}).get('assessed')
               for r in apt_results):
            building_checks.append(BuildingCheckResult(
                check_name='acoustic_separation',
                pass_=acoustic_pass,
                value=float(n - acoustic_fail_count) / n,
                required=1.0,
                description='%d of %d apartments outside noise influence zones (%s)' % (
                    n - acoustic_fail_count, n,
                    'ADG D16 / NSW 4H: >=3m from noise sources to bedrooms'
                ),
            ))
        else:
            building_checks.append(BuildingCheckResult(
                check_name='acoustic_separation',
                pass_=True,
                value=0.0,
                required=0.0,
                description='Acoustic: no APT_NOISE_* layers -- add noise source lines to assess (ADG D16 / NSW 4H)',
            ))

    # 9. Facade glazing ratio (if APT_WALL_EXTERNAL geometry present)
    facade_min_pct = thresholds.get('facade_glazing_min_pct')
    total_wall_area = 0.0
    total_win_area  = 0.0
    for r in apt_results:
        meta = r.checks.get('meta', {}) if isinstance(r.checks, dict) else {}
        bg = r.checks.get('building_geometry', []) if isinstance(r.checks, dict) else []
        if bg:
            import math
            for geom in bg:
                if geom.get('layer') == 'APT_WALL_EXTERNAL':
                    verts = geom.get('vertices', [])
                    if len(verts) >= 2:
                        for i in range(len(verts)):
                            p1 = verts[i]
                            p2 = verts[(i+1) % len(verts)]
                            seg = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
                            total_wall_area += seg  # perimeter as proxy for area
        # Window areas from energy data
        en = r.checks.get('energy', {}) if isinstance(r.checks, dict) else {}
        for win in en.get('window_list', []):
            total_win_area += win.get('win_area_m2', 0)

    if total_wall_area > 0:
        glazing_ratio = total_win_area / total_wall_area if total_wall_area > 0 else 0
        if facade_min_pct is not None:
            building_checks.append(BuildingCheckResult(
                check_name='facade_glazing',
                pass_=(glazing_ratio >= facade_min_pct),
                value=glazing_ratio,
                required=facade_min_pct,
                description='Facade glazing ratio: %.0f%% (window area vs external wall perimeter)' % (
                    glazing_ratio * 100
                ),
            ))
        else:
            building_checks.append(BuildingCheckResult(
                check_name='facade_glazing',
                pass_=True,
                value=glazing_ratio,
                required=0.0,
                description='Facade glazing: %.0f%% window-to-wall ratio (informational)' % (
                    glazing_ratio * 100
                ),
            ))

    # 10. Apartment mix diversity (informational)
    studio_count = mix.get('studio', 0)
    bed1_count   = mix.get('1bed', 0)
    bed2_count   = mix.get('2bed', 0)
    bed3_count   = mix.get('3bed+', 0)
    family_pct   = (bed2_count + bed3_count) / n if n > 0 else 0
    mix_types    = sum(1 for k, v in mix.items() if v > 0)
    mix_dominant = max(mix, key=mix.get)
    dominant_pct = mix[mix_dominant] / n if n > 0 else 0
    building_checks.append(BuildingCheckResult(
        check_name='apartment_mix',
        pass_=True,   # informational -- no hard threshold in ADG
        value=float(mix_types),
        required=0.0,
        description='Apartment mix: %s -- %d type(s), %d%% family (2bed+). %s' % (
            ', '.join('%d%s' % (v, k) for k, v in mix.items() if v > 0),
            mix_types,
            int(family_pct * 100),
            'Good diversity' if mix_types >= 3 else ('Single type -- consider diversity' if mix_types == 1 else 'Two types')
        ),
    ))

    # 11. Apartments per lift (NSW ADG 4F: 10+ storeys, max 40 apts per lift)
    max_per_lift = thresholds.get('max_apts_per_lift')
    min_storeys  = thresholds.get('min_storeys_for_lift_check', 10)
    if max_per_lift is not None:
        if storey_count >= min_storeys:
            # Hard check: we know it's a tall building
            building_checks.append(BuildingCheckResult(
                check_name='apartments_per_lift',
                pass_=True,  # informational -- can't count lifts from floor plan
                value=0.0,
                required=float(max_per_lift),
                description='NSW 4F: %d+ storey building -- max %d apartments per lift. Verify lift count on plans.' % (
                    min_storeys, max_per_lift
                ),
            ))
        elif storey_count > 0:
            building_checks.append(BuildingCheckResult(
                check_name='apartments_per_lift',
                pass_=True,
                value=0.0,
                required=0.0,
                description='Lift check: %d storeys -- below %d storey threshold for NSW 4F lift ratio' % (
                    storey_count, min_storeys
                ),
            ))
        else:
            building_checks.append(BuildingCheckResult(
                check_name='apartments_per_lift',
                pass_=True,
                value=0.0,
                required=0.0,
                description='NSW 4F: enter storey count in panel to check lift requirements',
            ))

    # Find worst unit
    worst_unit = None
    if apt_results:
        worst = max(apt_results, key=_summary_failures)
        if _summary_failures(worst) > 0:
            worst_unit = worst.unit_id

    return BuildingSummary(
        jurisdiction=jurisdiction,
        total_units=n,
        passing_units=passing_units,
        pass_rate=pass_rate,
        building_checks=building_checks,
        apartment_mix=mix,
        cross_ventilation_pct=cv_pct,
        adaptable_dwellings_pct=adaptable_pct,
        north_living_pct=north_pct,
        solar_access_pct=solar_pct,
        worst_unit=worst_unit,
        errors=errors,
    )


def _slim_results(results: dict) -> dict:
    """Remove heavy geometry/grid fields before storing in session.
    building_geometry is KEPT so wall/column geometry renders in the browser.
    """
    # Strip only diagnostics (verbose) and fixtures (heavy vertex data)
    _STRIP_KEYS = {'diagnostics'}
    _STRIP_DF_GRID = True
    slim = {k: v for k, v in results.items() if k not in _STRIP_KEYS}
    if _STRIP_DF_GRID and 'daylight' in slim:
        dl = dict(slim['daylight'])
        dl['rooms'] = [
            {rk: rv for rk, rv in room.items() if rk != 'df_grid'}
            for room in dl.get('rooms', [])
        ]
        slim['daylight'] = dl
    return slim


def check_building(
    dxf_text: str,
    jurisdiction: str = 'VIC',
    ceiling_h: float = 2.7,
    storey_count: int = 0,
    compliance_engine_fn=None,
) -> 'BuildingResult':
    """
    Run compliance checks on a multi-apartment DXF.

    Args:
        dxf_text:              Raw DXF file content as a string.
        jurisdiction:          'VIC', 'NSW', or 'BEST_PRACTICE'.
        ceiling_h:             Ceiling height in metres (default 2.7).
        compliance_engine_fn:  The run_compliance() callable from
                               compliance_engine.py. Must be provided.

    Returns:
        BuildingResult with per-apartment results and building-level summary.
    """
    if compliance_engine_fn is None:
        raise NotImplementedError(
            "Pass compliance_engine_fn=run_compliance from compliance_engine.py"
        )

    jurisdiction = jurisdiction.upper()
    unit_ids = detect_unit_ids(dxf_text)
    if not unit_ids:
        raise ValueError(
            "No multi-apartment unit prefixes found "
            "(e.g. APT_01_ROOM_MAINBED). "
            "Use /api/check for single-apartment files."
        )

    apt_results: List[UnitResult] = []

    for unit_id in unit_ids:
        unit_dxf = extract_unit_dxf(dxf_text, unit_id)
        apt_lines = [l.strip() for l in unit_dxf.splitlines() if l.strip().startswith('APT_') and 'SKIP' not in l]
        try:
            raw = compliance_engine_fn(
                unit_dxf,
                ceiling_h=ceiling_h,
                jurisdiction=jurisdiction,
            )
        except Exception as exc:
            apt_results.append(UnitResult(
                unit_id=unit_id,
                jurisdiction=jurisdiction,
                pass_overall=False,
                bedroom_count=0,
                apartment_type='unknown',
                has_cross_ventilation=False,
                is_adaptable=False,
                has_north_living=False,
                summary={'error': str(exc)},
                checks={'error': str(exc)},
            ))
            continue

        if 'error' in raw and raw['error']:
            apt_results.append(UnitResult(
                unit_id=unit_id,
                jurisdiction=jurisdiction,
                pass_overall=False,
                bedroom_count=0,
                apartment_type='unknown',
                has_cross_ventilation=False,
                is_adaptable=False,
                has_north_living=False,
                summary={'error': raw['error']},
                checks=raw,
            ))
            continue

        # --- Pull the fields we need from the real engine output ---

        summary = raw.get('summary', {})
        meta    = raw.get('meta', {})

        pass_overall = summary.get('all_pass', False)
        bedroom_count = meta.get('bedroom_count', 0)

        # Cross-ventilation: results['ventilation']['cross_ventilation']['pass']
        has_cv = (
            raw.get('ventilation', {})
               .get('cross_ventilation', {})
               .get('pass', False)
        )

        # Adaptable: results['accessibility']['overall_pass']
        is_adaptable = raw.get('accessibility', {}).get('overall_pass', False)

        # North-facing living: results['energy']['living_north']
        has_north_living = raw.get('energy', {}).get('living_north', False)

        apt_results.append(UnitResult(
            unit_id=unit_id,
            jurisdiction=jurisdiction,
            pass_overall=pass_overall,
            bedroom_count=bedroom_count,
            apartment_type=_apartment_type(bedroom_count),
            has_cross_ventilation=has_cv,
            is_adaptable=is_adaptable,
            has_north_living=has_north_living,
            summary=summary,
            checks=_slim_results(raw),
        ))

    building_summary = aggregate_building(apt_results, jurisdiction, storey_count=storey_count)

    return BuildingResult(
        apartments=apt_results,
        summary=building_summary,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def building_result_to_dict(result: 'BuildingResult') -> dict:
    """Serialise BuildingResult to a plain dict for JSON responses."""

    def unit_to_dict(u: UnitResult) -> dict:
        return {
            'unit_id': u.unit_id,
            'jurisdiction': u.jurisdiction,
            'pass': u.pass_overall,
            'apartment_type': u.apartment_type,
            'bedroom_count': u.bedroom_count,
            'has_cross_ventilation': u.has_cross_ventilation,
            'is_adaptable': u.is_adaptable,
            'has_north_living': u.has_north_living,
            'summary': u.summary,
            'checks': u.checks,
        }

    def bcheck_to_dict(c: BuildingCheckResult) -> dict:
        return {
            'check_name': c.check_name,
            'pass': c.pass_,
            'value': round(c.value, 4),
            'required': round(c.required, 4),
            'value_pct': '%d%%' % int(c.value * 100),
            'required_pct': '%d%%' % int(c.required * 100),
            'description': c.description,
        }

    s = result.summary
    return {
        'apartments': [unit_to_dict(u) for u in result.apartments],
        'summary': {
            'jurisdiction': s.jurisdiction,
            'total_units': s.total_units,
            'passing_units': s.passing_units,
            'pass_rate': round(s.pass_rate, 4),
            'pass_rate_pct': '%d%%' % int(s.pass_rate * 100),
            'cross_ventilation_pct': round(s.cross_ventilation_pct, 4),
            'adaptable_dwellings_pct': round(s.adaptable_dwellings_pct, 4),
            'north_living_pct': round(s.north_living_pct, 4),
            'solar_access_pct': round(s.solar_access_pct, 4),
            'apartment_mix': s.apartment_mix,
            'worst_unit': s.worst_unit,
            'building_checks': [bcheck_to_dict(c) for c in s.building_checks],
            'errors': s.errors,
        },
    }