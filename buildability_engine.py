"""
Buildability Engine for Apt.
Processes wall centrelines and opening data from DXF, segments walls into
prefab panel runs, handles junctions, and tiles floor panels.

Input:  wall centrelines (list of segments), openings (doors/windows),
        room polygons, building_system.json
Output: panel assignments per wall run, floor tile assignments, report data
"""

import json
import math
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ─────────────────────────────────────────────────────────────────────────────

SNAP_TOL = 50  # mm — points within this are treated as coincident


def dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def seg_length(seg):
    return dist(seg[0], seg[1])


def pts_equal(a, b, tol=SNAP_TOL):
    return dist(a, b) <= tol


def seg_angle(seg):
    dx = seg[1][0] - seg[0][0]
    dy = seg[1][1] - seg[0][1]
    return math.degrees(math.atan2(dy, dx)) % 180


def project_point_onto_seg(p, seg):
    """Returns parameter t in [0,1] of p projected onto seg, and the foot point."""
    ax, ay = seg[0]
    bx, by = seg[1]
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-9:
        return 0.0, seg[0]
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    foot = (ax + t * dx, ay + t * dy)
    return t, foot


def line_intersection(seg1, seg2, tol=SNAP_TOL):
    """Returns intersection point of infinite lines through seg1 and seg2, or None."""
    x1, y1 = seg1[0]
    x2, y2 = seg1[1]
    x3, y3 = seg2[0]
    x4, y4 = seg2[1]
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-9:
        return None  # parallel
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    ix = x1 + t * (x2 - x1)
    iy = y1 + t * (y2 - y1)
    return (ix, iy)


def point_on_seg(p, seg, tol=SNAP_TOL):
    """Is point p on segment seg (within tol)?"""
    foot_t, foot = project_point_onto_seg(p, seg)
    return dist(p, foot) <= tol and -tol <= foot_t * seg_length(seg) <= seg_length(seg) + tol


# ─────────────────────────────────────────────────────────────────────────────
# Wall Network — build connected wall runs from raw segments
# ─────────────────────────────────────────────────────────────────────────────

class WallSegment:
    def __init__(self, idx, start, end):
        self.idx = idx
        self.start = start
        self.end = end
        self.length = dist(start, end)
        self.angle = seg_angle((start, end))
        self.openings = []        # list of Opening objects projected onto this seg
        self.panels = []          # assigned panel objects after nesting
        self.run_id = None        # which WallRun this belongs to

    def as_seg(self):
        return (self.start, self.end)

    def to_dict(self):
        return {
            "idx": self.idx,
            "start": list(self.start),
            "end": list(self.end),
            "length": round(self.length),
            "angle": round(self.angle, 1),
            "run_id": self.run_id,
            "panels": [p.to_dict() for p in self.panels],
            "openings": [o.to_dict() for o in self.openings],
        }


class Opening:
    def __init__(self, otype, center, width, height=None):
        self.type = otype        # 'door' or 'window'
        self.center = center     # (x, y) world coords
        self.width = width       # mm
        self.height = height     # mm, optional
        self.t_param = None      # 0–1 position along parent wall segment
        self.wall_idx = None

    def to_dict(self):
        return {
            "type": self.type,
            "center": list(self.center),
            "width": self.width,
            "t_param": self.t_param,
            "wall_idx": self.wall_idx,
        }


class WallRun:
    """A contiguous collinear chain of WallSegments that share endpoints."""
    def __init__(self, run_id, segments):
        self.run_id = run_id
        self.segments = segments
        self.total_length = sum(s.length for s in segments)
        for s in segments:
            s.run_id = run_id

    def to_dict(self):
        return {
            "run_id": self.run_id,
            "total_length": round(self.total_length),
            "segments": [s.to_dict() for s in self.segments],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Junction detection and wall trimming
# ─────────────────────────────────────────────────────────────────────────────

def find_junctions(segments, wall_thickness):
    """
    Detect T-junctions and corners.
    Returns list of junction dicts with type, point, and involved segment indices.

    Convention: at a T-junction, the 'through' wall is the longer one;
    the 'stub' wall terminates at the through-wall face.
    The stub wall end is trimmed back by wall_thickness/2 so panels don't overlap.
    """
    junctions = []
    n = len(segments)
    tol = SNAP_TOL

    for i in range(n):
        for j in range(i + 1, n):
            si = segments[i]
            sj = segments[j]
            angle_diff = abs(si.angle - sj.angle) % 180

            # Skip parallel walls
            if angle_diff < 5 or angle_diff > 175:
                continue

            pt = line_intersection(si.as_seg(), sj.as_seg())
            if pt is None:
                continue

            # Is intersection within or near both segments?
            ti, _ = project_point_onto_seg(pt, si.as_seg())
            tj, _ = project_point_onto_seg(pt, sj.as_seg())

            i_on = -tol / si.length <= ti <= 1 + tol / si.length
            j_on = -tol / sj.length <= tj <= 1 + tol / sj.length

            if not (i_on and j_on):
                continue

            # Classify junction type
            i_is_end = ti < tol / si.length or ti > 1 - tol / si.length
            j_is_end = tj < tol / sj.length or tj > 1 - tol / sj.length

            if i_is_end and j_is_end:
                jtype = "L_corner"
            elif not i_is_end and j_is_end:
                jtype = "T_junction"   # i is through, j is stub
            elif i_is_end and not j_is_end:
                jtype = "T_junction"   # j is through, i is stub
            else:
                jtype = "X_cross"

            junctions.append({
                "type": jtype,
                "point": pt,
                "seg_i": i,
                "seg_j": j,
                "t_i": ti,
                "t_j": tj,
            })

    return junctions


def trim_stub_ends(segments, junctions, wall_thickness):
    """
    At T-junctions, trim the stub wall back by half the through-wall thickness
    so panel runs terminate correctly at the face of the through wall.
    Modifies segment start/end in place.
    """
    half_t = wall_thickness / 2

    for junc in junctions:
        if junc["type"] != "T_junction":
            continue

        i, j = junc["seg_i"], junc["seg_j"]
        ti, tj = junc["t_i"], junc["t_j"]
        pt = junc["point"]

        # Determine which is stub (ends at junction)
        if 0.05 < ti < 0.95:
            # i is through, j is stub
            stub = segments[j]
            stub_t = tj
        else:
            # j is through, i is stub
            stub = segments[i]
            stub_t = ti

        # Trim the end of the stub that touches the junction
        dx = stub.end[0] - stub.start[0]
        dy = stub.end[1] - stub.start[1]
        length = dist(stub.start, stub.end)
        if length < 1:
            continue
        ux, uy = dx / length, dy / length  # unit vector along stub

        if stub_t > 0.5:
            # Junction is at the 'end' of the stub
            new_end = (stub.end[0] - ux * half_t, stub.end[1] - uy * half_t)
            stub.end = new_end
        else:
            # Junction is at the 'start' of the stub
            new_start = (stub.start[0] + ux * half_t, stub.start[1] + uy * half_t)
            stub.start = new_start

        stub.length = dist(stub.start, stub.end)


# ─────────────────────────────────────────────────────────────────────────────
# Opening projection onto walls
# ─────────────────────────────────────────────────────────────────────────────

def project_openings_onto_walls(segments, openings, tol=300):
    """For each opening, find the closest wall segment and project it on."""
    for op in openings:
        best_seg = None
        best_dist = float('inf')
        best_t = None
        for seg in segments:
            t, foot = project_point_onto_seg(op.center, seg.as_seg())
            d = dist(op.center, foot)
            if d < best_dist:
                best_dist = d
                best_seg = seg
                best_t = t
        if best_seg is not None and best_dist < tol:
            op.wall_idx = best_seg.idx
            op.t_param = best_t
            best_seg.openings.append(op)


# ─────────────────────────────────────────────────────────────────────────────
# Panel nesting algorithms
# ─────────────────────────────────────────────────────────────────────────────

class Panel:
    def __init__(self, panel_def, position_mm, is_cut=False, custom=False):
        self.id = panel_def["id"]
        self.label = panel_def["label"]
        self.length = panel_def["length"]
        self.crane_lift = panel_def.get("crane_lift", True)
        self.install_minutes = panel_def.get("install_minutes", 45)
        self.position_mm = position_mm  # offset from wall start in mm
        self.is_cut = is_cut            # requires site cutting
        self.custom = custom
        self.actual_length = panel_def["length"]  # overridden for cuts

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "length": self.length,
            "actual_length": self.actual_length,
            "position_mm": round(self.position_mm),
            "is_cut": self.is_cut,
            "crane_lift": self.crane_lift,
            "install_minutes": self.install_minutes,
        }


def nest_wall_greedy(wall_length, openings_on_wall, panel_defs, tol=50):
    """
    Mode 1 — Greedy longest-first.
    Fills available spans (gaps between openings) with the longest fitting panel,
    working left to right. Returns list of Panel objects.
    """
    panels_by_length = sorted(panel_defs, key=lambda p: p["length"], reverse=True)
    spans = _get_solid_spans(wall_length, openings_on_wall)
    result = []
    for span_start, span_end in spans:
        cursor = span_start
        span_len = span_end - span_start
        while cursor < span_end - tol:
            remaining = span_end - cursor
            placed = False
            for pdef in panels_by_length:
                if pdef["length"] <= remaining + tol:
                    p = Panel(pdef, cursor)
                    if pdef["length"] > remaining:
                        p.actual_length = remaining
                        p.is_cut = True
                    result.append(p)
                    cursor += p.actual_length
                    placed = True
                    break
            if not placed:
                # smallest panel is bigger than remaining — use it cut
                pdef = panels_by_length[-1]
                p = Panel(pdef, cursor, is_cut=True)
                p.actual_length = remaining
                result.append(p)
                cursor = span_end
    return result


def nest_wall_min_variety(wall_length, openings_on_wall, panel_defs, tol=50):
    """
    Mode 2 — Minimise variety.
    Tries to fill all spans using as few distinct panel types as possible.
    Strategy: find the single panel length that, possibly with one cut, covers
    each span most efficiently; then pick the set of lengths that minimises
    distinct types across all spans.
    """
    # Score each panel type against each span, pick per-span best type,
    # then find the panel type that works for the most spans.
    panels_by_length = sorted(panel_defs, key=lambda p: p["length"], reverse=True)
    spans = _get_solid_spans(wall_length, openings_on_wall)

    # For each span, find the panel length that tiles it with least waste
    span_best = []
    for span_start, span_end in spans:
        span_len = span_end - span_start
        best_score = float('inf')
        best_pdef = panels_by_length[0]
        for pdef in panels_by_length:
            n_full = int(span_len // pdef["length"])
            remainder = span_len - n_full * pdef["length"]
            # Score: primarily waste, secondarily use of cut (0 = no cut)
            waste = remainder if remainder > tol else 0
            score = waste + (100 if remainder > tol else 0)
            if score < best_score:
                best_score = score
                best_pdef = pdef
        span_best.append((span_start, span_end, best_pdef))

    # Now nest each span with its best panel type
    result = []
    for span_start, span_end, pdef in span_best:
        cursor = span_start
        while cursor < span_end - tol:
            remaining = span_end - cursor
            p = Panel(pdef, cursor)
            if pdef["length"] <= remaining + tol:
                if pdef["length"] > remaining:
                    p.actual_length = remaining
                    p.is_cut = True
                result.append(p)
                cursor += p.actual_length
            else:
                p.actual_length = remaining
                p.is_cut = True
                result.append(p)
                cursor = span_end
    return result


def _get_solid_spans(wall_length, openings_on_wall):
    """
    Returns list of (start_mm, end_mm) solid spans between openings.
    Openings are given as (t_param, width_mm) along the wall.
    """
    gaps = []
    for op in openings_on_wall:
        t = op.t_param if op.t_param is not None else 0.5
        half_w = op.width / 2
        gap_start = t * wall_length - half_w
        gap_end = t * wall_length + half_w
        gaps.append((max(0, gap_start), min(wall_length, gap_end)))

    # Sort and merge overlapping gaps
    gaps.sort()
    merged = []
    for g in gaps:
        if merged and g[0] < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], g[1]))
        else:
            merged.append(list(g))

    # Invert gaps to get solid spans
    spans = []
    cursor = 0.0
    for gap_start, gap_end in merged:
        if gap_start > cursor + 10:
            spans.append((cursor, gap_start))
        cursor = gap_end
    if cursor < wall_length - 10:
        spans.append((cursor, wall_length))

    return spans


# ─────────────────────────────────────────────────────────────────────────────
# Floor panel tiling
# ─────────────────────────────────────────────────────────────────────────────

class FloorPanel:
    def __init__(self, panel_def, x, y, rotated=False, is_cut_x=False, is_cut_y=False):
        self.id = panel_def["id"]
        self.label = panel_def["label"]
        self.length = panel_def["length"]
        self.width = panel_def["width"]
        self.crane_lift = panel_def.get("crane_lift", True)
        self.install_minutes = panel_def.get("install_minutes", 55)
        self.x = x            # bottom-left world x
        self.y = y            # bottom-left world y
        self.rotated = rotated
        self.actual_length = panel_def["length"]
        self.actual_width = panel_def["width"]
        self.is_cut = is_cut_x or is_cut_y

    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "length": self.actual_length,
            "width": self.actual_width,
            "x": round(self.x),
            "y": round(self.y),
            "rotated": self.rotated,
            "is_cut": self.is_cut,
            "crane_lift": self.crane_lift,
            "install_minutes": self.install_minutes,
        }


def tile_floor_room(room_bbox, floor_panel_defs, direction="auto", tol=50):
    """
    Tiles floor panels across a room bounding box.
    room_bbox: (min_x, min_y, max_x, max_y) in mm
    direction: "x", "y", or "auto" (picks longest span)
    Returns list of FloorPanel objects.
    """
    min_x, min_y, max_x, max_y = room_bbox
    room_w = max_x - min_x
    room_h = max_y - min_y

    # Auto-pick direction: panels span the shorter dimension
    if direction == "auto":
        direction = "x" if room_w >= room_h else "y"

    # Use the largest floor panel by area as primary
    primary = sorted(floor_panel_defs, key=lambda p: p["length"] * p["width"], reverse=True)[0]

    # Panel long dimension runs in 'direction', short dimension perpendicular
    if direction == "x":
        span_length = room_w    # panels laid along x
        span_width = room_h     # perpendicular (y)
        panel_long = primary["length"]
        panel_short = primary["width"]
    else:
        span_length = room_h
        span_width = room_w
        panel_long = primary["length"]
        panel_short = primary["width"]

    tiles = []
    # Tile along perpendicular axis (rows)
    y_cursor = 0.0
    while y_cursor < span_width - tol:
        row_width = min(panel_short, span_width - y_cursor)
        is_cut_y = row_width < panel_short - tol

        # Tile along primary axis (columns in this row)
        x_cursor = 0.0
        while x_cursor < span_length - tol:
            col_length = min(panel_long, span_length - x_cursor)
            is_cut_x = col_length < panel_long - tol

            pdef = dict(primary)
            pdef["length"] = int(round(col_length))
            pdef["width"] = int(round(row_width))

            if direction == "x":
                wx = min_x + x_cursor
                wy = min_y + y_cursor
            else:
                wx = min_x + y_cursor
                wy = min_y + x_cursor

            fp = FloorPanel(pdef, wx, wy, rotated=(direction == "y"),
                            is_cut_x=is_cut_x, is_cut_y=is_cut_y)
            fp.actual_length = int(round(col_length))
            fp.actual_width = int(round(row_width))
            tiles.append(fp)
            x_cursor += col_length

        y_cursor += row_width

    return tiles


# ─────────────────────────────────────────────────────────────────────────────
# Main engine entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_buildability(wall_segments_raw, openings_raw, room_bboxes, system_json, mode="greedy"):
    """
    wall_segments_raw: list of {"start":[x,y], "end":[x,y]}
    openings_raw: list of {"type":"door"|"window", "center":[x,y], "width":mm}
    room_bboxes: list of {"room_id":str, "bbox":[minx,miny,maxx,maxy]}
    system_json: parsed building_system.json dict
    mode: "greedy" | "min_variety"
    Returns full result dict.
    """
    wall_thickness = system_json["system_defaults"]["wall_thickness"]
    wall_panels = system_json["wall_panels"]
    floor_panels = system_json["floor_panels"]

    # Build WallSegment objects
    segments = []
    for idx, raw in enumerate(wall_segments_raw):
        seg = WallSegment(idx, tuple(raw["start"]), tuple(raw["end"]))
        segments.append(seg)

    # Build Opening objects
    openings = []
    for raw in openings_raw:
        op = Opening(raw["type"], tuple(raw["center"]), raw["width"])
        openings.append(op)

    # Detect and resolve junctions
    junctions = find_junctions(segments, wall_thickness)
    trim_stub_ends(segments, junctions, wall_thickness)

    # Project openings onto walls
    project_openings_onto_walls(segments, openings)

    # Nest panels onto each wall segment
    nest_fn = nest_wall_greedy if mode == "greedy" else nest_wall_min_variety
    for seg in segments:
        seg.panels = nest_fn(seg.length, seg.openings, wall_panels)

    # Tile floors
    floor_tiles = {}
    for room in room_bboxes:
        tiles = tile_floor_room(room["bbox"], floor_panels)
        floor_tiles[room["room_id"]] = [t.to_dict() for t in tiles]

    # Build report
    report = _build_report(segments, floor_tiles, junctions)

    return {
        "wall_segments": [s.to_dict() for s in segments],
        "floor_tiles": floor_tiles,
        "junctions": [
            {"type": j["type"], "point": list(j["point"]),
             "seg_i": j["seg_i"], "seg_j": j["seg_j"]}
            for j in junctions
        ],
        "report": report,
        "mode": mode,
    }


def _build_report(segments, floor_tiles, junctions):
    all_wall_panels = []
    for seg in segments:
        all_wall_panels.extend(seg.panels)

    all_floor_panels = []
    for room_id, tiles in floor_tiles.items():
        all_floor_panels.extend(tiles)

    # Count wall panels by type
    wall_type_counts = {}
    for p in all_wall_panels:
        key = p.id
        if key not in wall_type_counts:
            wall_type_counts[key] = {"id": p.id, "label": p.label,
                                      "count": 0, "cut_count": 0,
                                      "total_install_min": 0, "crane_lifts": 0}
        wall_type_counts[key]["count"] += 1
        if p.is_cut:
            wall_type_counts[key]["cut_count"] += 1
        wall_type_counts[key]["total_install_min"] += p.install_minutes
        if p.crane_lift:
            wall_type_counts[key]["crane_lifts"] += 1

    floor_type_counts = {}
    for p in all_floor_panels:
        key = p["id"]
        if key not in floor_type_counts:
            floor_type_counts[key] = {"id": p["id"], "label": p["label"],
                                       "count": 0, "cut_count": 0,
                                       "total_install_min": 0, "crane_lifts": 0}
        floor_type_counts[key]["count"] += 1
        if p["is_cut"]:
            floor_type_counts[key]["cut_count"] += 1
        floor_type_counts[key]["total_install_min"] += p["install_minutes"]
        if p["crane_lift"]:
            floor_type_counts[key]["crane_lifts"] += 1

    total_crane_lifts = (
        sum(v["crane_lifts"] for v in wall_type_counts.values()) +
        sum(v["crane_lifts"] for v in floor_type_counts.values())
    )
    total_install_min = (
        sum(v["total_install_min"] for v in wall_type_counts.values()) +
        sum(v["total_install_min"] for v in floor_type_counts.values())
    )
    total_wall_panels = sum(v["count"] for v in wall_type_counts.values())
    total_floor_panels = sum(v["count"] for v in floor_type_counts.values())
    cut_wall = sum(v["cut_count"] for v in wall_type_counts.values())
    cut_floor = sum(v["cut_count"] for v in floor_type_counts.values())
    wall_variety = len(wall_type_counts)
    junction_count = len(junctions)

    return {
        "summary": {
            "total_wall_panels": total_wall_panels,
            "total_floor_panels": total_floor_panels,
            "total_panels": total_wall_panels + total_floor_panels,
            "wall_panel_types": wall_variety,
            "total_crane_lifts": total_crane_lifts,
            "estimated_install_hours": round(total_install_min / 60, 1),
            "cut_wall_panels": cut_wall,
            "cut_floor_panels": cut_floor,
            "junction_count": junction_count,
        },
        "wall_schedule": sorted(wall_type_counts.values(), key=lambda x: x["id"]),
        "floor_schedule": sorted(floor_type_counts.values(), key=lambda x: x["id"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DXF reader (extends existing apt DXF parser pattern)
# ─────────────────────────────────────────────────────────────────────────────

def parse_dxf_for_buildability(dxf_content_str):
    """
    Reads a DXF string and extracts:
    - Wall centrelines from layer WALLS (LINE entities)
    - Door rectangles from layer DOORS (INSERT or LWPOLYLINE)
    - Window rectangles from layer WINDOWS
    - Room bounding boxes from layer ROOMS (LWPOLYLINE)
    Returns dicts suitable for run_buildability().
    """
    try:
        import ezdxf
        import io
        doc = ezdxf.read(io.StringIO(dxf_content_str))
        msp = doc.modelspace()
    except Exception as e:
        return None, str(e)

    wall_segments = []
    openings = []
    room_bboxes = []

    for entity in msp:
        layer = entity.dxf.layer.upper()

        if layer == "WALLS" and entity.dxftype() == "LINE":
            s = entity.dxf.start
            e = entity.dxf.end
            wall_segments.append({"start": [s.x, s.y], "end": [e.x, e.y]})

        elif layer == "DOORS" and entity.dxftype() == "LWPOLYLINE":
            pts = list(entity.get_points())
            if len(pts) >= 2:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                cx = (min(xs) + max(xs)) / 2
                cy = (min(ys) + max(ys)) / 2
                w = max(abs(max(xs) - min(xs)), abs(max(ys) - min(ys)))
                openings.append({"type": "door", "center": [cx, cy], "width": w})

        elif layer == "WINDOWS" and entity.dxftype() == "LWPOLYLINE":
            pts = list(entity.get_points())
            if len(pts) >= 2:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                cx = (min(xs) + max(xs)) / 2
                cy = (min(ys) + max(ys)) / 2
                w = max(abs(max(xs) - min(xs)), abs(max(ys) - min(ys)))
                openings.append({"type": "window", "center": [cx, cy], "width": w})

        elif layer == "ROOMS" and entity.dxftype() == "LWPOLYLINE":
            pts = list(entity.get_points())
            if len(pts) >= 3:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                room_id = entity.dxf.get("layer", "ROOM") + f"_{len(room_bboxes)}"
                room_bboxes.append({
                    "room_id": room_id,
                    "bbox": [min(xs), min(ys), max(xs), max(ys)]
                })

    return {"wall_segments": wall_segments, "openings": openings, "room_bboxes": room_bboxes}, None