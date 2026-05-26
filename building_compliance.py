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
        'cross_ventilation_pct': 0.40,   # ADG Vic 2017 s.55
        'adaptable_dwellings_pct': 0.50, # BCA D3.3
    },
    'NSW': {
        'cross_ventilation_pct': 0.60,   # NSW ADG 2015 p.88
        'adaptable_dwellings_pct': 0.50,
    },
    'BEST_PRACTICE': {
        'cross_ventilation_pct': 0.60,
        'adaptable_dwellings_pct': 0.50,
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
    apartment_mix: Dict[str, int]   # {'studio': 1, '1bed': 3, '2bed': 4, '3bed+': 2}
    cross_ventilation_pct: float
    adaptable_dwellings_pct: float
    north_living_pct: float
    worst_unit: Optional[str]       # unit_id with most summary failures
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


def extract_unit_dxf(dxf_text: str, unit_id: str) -> str:
    """
    Return a version of the DXF with layer names normalised for one unit.

    APT_01_ROOM_MAINBED  ->  APT_ROOM_MAINBED
    APT_01_WINDOW_LIVING ->  APT_WINDOW_LIVING

    All other lines are passed through unchanged so the DXF structure
    (HEADER, ENTITIES sections etc.) remains valid.
    """
    # Build a targeted regex for this specific unit
    unit_re = re.compile(
        r'^(APT_)' + re.escape(unit_id) + r'_((?:ROOM|STORAGE|POS|WINDOW|DOOR|NORTH|NOISE|'
        r'WALL|COLUMN|OVERHANG|SHAFT|KITCHEN|BATHROOM|FURNITURE).*)$'
    )
    out = []
    for line in dxf_text.splitlines():
        m = unit_re.match(line.strip())
        if m:
            # Normalise: APT_01_ROOM_MAINBED -> APT_ROOM_MAINBED
            out.append(f"APT_{m.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


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

def aggregate_building(
    apt_results: List[UnitResult],
    jurisdiction: str = 'VIC',
) -> BuildingSummary:
    thresholds = BUILDING_THRESHOLDS.get(
        jurisdiction.upper(),
        BUILDING_THRESHOLDS['VIC']
    )
    n = len(apt_results)
    errors: List[str] = []

    if n == 0:
        errors.append('No apartment results to aggregate.')
        return BuildingSummary(
            jurisdiction=jurisdiction, total_units=0, passing_units=0,
            pass_rate=0.0, building_checks=[], apartment_mix={},
            cross_ventilation_pct=0.0, adaptable_dwellings_pct=0.0,
            north_living_pct=0.0, worst_unit=None, errors=errors,
        )

    passing_units    = sum(1 for r in apt_results if r.pass_overall)
    cv_units         = sum(1 for r in apt_results if r.has_cross_ventilation)
    adaptable_units  = sum(1 for r in apt_results if r.is_adaptable)
    north_liv_units  = sum(1 for r in apt_results if r.has_north_living)

    cv_pct       = cv_units / n
    adaptable_pct = adaptable_units / n
    north_pct    = north_liv_units / n
    pass_rate    = passing_units / n

    # Apartment mix
    mix: Dict[str, int] = {'studio': 0, '1bed': 0, '2bed': 0, '3bed+': 0}
    for r in apt_results:
        mix[r.apartment_type] += 1

    # Building-level checks
    building_checks: List[BuildingCheckResult] = []

    cv_req = thresholds['cross_ventilation_pct']
    building_checks.append(BuildingCheckResult(
        check_name='cross_ventilation',
        pass_=cv_pct >= cv_req,
        value=cv_pct,
        required=cv_req,
        description=(
            f'{cv_units} of {n} apartments achieve cross ventilation '
            f'({cv_pct:.0%}) — {jurisdiction} requires {cv_req:.0%}'
        ),
    ))

    ad_req = thresholds['adaptable_dwellings_pct']
    building_checks.append(BuildingCheckResult(
        check_name='adaptable_dwellings',
        pass_=adaptable_pct >= ad_req,
        value=adaptable_pct,
        required=ad_req,
        description=(
            f'{adaptable_units} of {n} apartments meet adaptability checks '
            f'({adaptable_pct:.0%}) — BCA D3.3 requires {ad_req:.0%}'
        ),
    ))

    # North-facing living (informational — no hard building-level threshold)
    building_checks.append(BuildingCheckResult(
        check_name='north_facing_living',
        pass_=True,   # informational only
        value=north_pct,
        required=0.0,
        description=(
            f'{north_liv_units} of {n} apartments have a north-facing '
            f'living-area window ({north_pct:.0%})'
        ),
    ))

    # Worst unit
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
        worst_unit=worst_unit,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Heavy result fields that contain geometry grids — strip these from the
# per-unit 'checks' payload to keep session storage lean.
_STRIP_KEYS = {'diagnostics', 'building_geometry', 'fixtures'}
_STRIP_DF_GRID = True   # also strip df_grid arrays from daylight rooms


def _slim_results(results: dict) -> dict:
    """Remove heavy geometry/grid fields before storing in session."""
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
        try:
            raw = compliance_engine_fn(
                unit_dxf,
                ceiling_h=ceiling_h,
                jurisdiction=jurisdiction,
            )
        except Exception as exc:
            import sys, traceback
            print('APT DEBUG unit %s EXCEPTION: %s' % (unit_id, traceback.format_exc()), file=sys.stderr, flush=True)
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

        import sys
        print('APT DEBUG unit %s: raw keys=%s error=%s' % (unit_id, list(raw.keys()) if isinstance(raw, dict) else type(raw), raw.get('error') if isinstance(raw, dict) else 'n/a'), file=sys.stderr, flush=True)

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

    building_summary = aggregate_building(apt_results, jurisdiction)

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
            'value_pct': f'{c.value:.0%}',
            'required_pct': f'{c.required:.0%}',
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
            'pass_rate_pct': f'{s.pass_rate:.0%}',
            'cross_ventilation_pct': round(s.cross_ventilation_pct, 4),
            'adaptable_dwellings_pct': round(s.adaptable_dwellings_pct, 4),
            'north_living_pct': round(s.north_living_pct, 4),
            'apartment_mix': s.apartment_mix,
            'worst_unit': s.worst_unit,
            'building_checks': [bcheck_to_dict(c) for c in s.building_checks],
            'errors': s.errors,
        },
    }