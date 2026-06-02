"""
app_site_routes.py  ·  FORMWORK site-intelligence blueprint
============================================================
Drop-in Flask blueprint for the apt-compliance-plugin repo. Gives the massing
simulator real, geolocated site data by proxying the authoritative Australian
cadastre / planning REST services server-side (the browser can't hit them
directly — CORS). Zero new dependencies: stdlib urllib only.

Register in your app factory / app.py:

    from app_site_routes import site_bp
    app.register_blueprint(site_bp)

Endpoints (all GET, all return JSON):
    /api/site/geocode?q=ADDRESS
    /api/site/parcel?lat=&lon=[&context=1]
    /api/site/zone?lat=&lon=[&state=VIC|NSW]
    /api/site/context?lat=&lon=[&radius=120]
    /api/site/resolve?q=ADDRESS          <- one-shot: geocode+parcel+zone+context

Notes / honest limitations:
  * Parcel geometry is authoritative (DCDB / Vicmap). Area, frontage, depth and
    orientation are derived locally via an equirectangular projection at the
    site latitude (sub-0.1% error at parcel scale) + PCA oriented bounding box,
    matching the PCA approach already used elsewhere in this repo.
  * Setbacks returned are sensible DEFAULTS keyed off the planning zone. The
    binding numbers live in each council's DCP / the planning scheme and are
    not a clean API, so treat these as a starting point, not a determination.
  * "Context" = neighbouring parcel footprints. True contextual building
    HEIGHTS need a buildings dataset (e.g. Geoscape Buildings, paid); footprints
    are extruded to a nominal height client-side until that's wired in.
"""

import json
import math
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request

site_bp = Blueprint("site", __name__)

# ----------------------------------------------------------------------------
# Service endpoints (open ArcGIS REST). Adjust here if a service moves.
# ----------------------------------------------------------------------------
NSW_CADASTRE_LOT = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Cadastre/MapServer/9"
NSW_ZONE = "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/EPI_Primary_Planning_Layers/MapServer/2"
NSW_FSR = "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/EPI_Primary_Planning_Layers/MapServer/4"
NSW_HOB = "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/EPI_Primary_Planning_Layers/MapServer/5"

VIC_PARCEL = "https://plan-gis.mapshare.vic.gov.au/arcgis/rest/services/Planning/VicPlan_PropertyAndParcel/MapServer/4"
VIC_ZONE = "https://plan-gis.mapshare.vic.gov.au/arcgis/rest/services/Planning/Vicplan_PlanningSchemeZones/MapServer/0"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Replace the contact below with your own (Nominatim usage policy requires it).
USER_AGENT = "FORMWORK-massing/1.0 (apt-compliance-plugin; contact: you@example.com)"

HTTP_TIMEOUT = 20
FLOOR_TO_FLOOR_M = 3.1  # used to convert a height-of-building limit into storeys


# ----------------------------------------------------------------------------
# Zone -> default planning parameters. Starting points, not determinations.
# ----------------------------------------------------------------------------
ZONE_DEFAULTS = {
    # Victoria
    "GRZ": dict(front=5.4, rear=4.0, side=1.0, upper=3.0, storeys=3, label="General Residential Zone"),
    "RGZ": dict(front=5.4, rear=5.0, side=2.0, upper=5.0, storeys=4, label="Residential Growth Zone"),
    "NRZ": dict(front=5.4, rear=4.0, side=1.0, upper=3.0, storeys=2, label="Neighbourhood Residential Zone"),
    "MUZ": dict(front=3.0, rear=4.5, side=3.0, upper=3.0, storeys=5, label="Mixed Use Zone"),
    "TZ":  dict(front=3.0, rear=4.5, side=3.0, upper=3.0, storeys=4, label="Township Zone"),
    "LDRZ": dict(front=7.6, rear=5.0, side=2.0, upper=3.0, storeys=2, label="Low Density Residential Zone"),
    "C1Z": dict(front=0.0, rear=3.0, side=0.0, upper=3.0, storeys=4, label="Commercial 1 Zone"),
    # NSW
    "R1": dict(front=6.0, rear=6.0, side=3.0, upper=3.0, storeys=4, label="General Residential"),
    "R2": dict(front=6.0, rear=6.0, side=1.5, upper=3.0, storeys=2, label="Low Density Residential"),
    "R3": dict(front=6.0, rear=6.0, side=3.0, upper=3.0, storeys=3, label="Medium Density Residential"),
    "R4": dict(front=6.0, rear=8.0, side=4.0, upper=5.0, storeys=6, label="High Density Residential"),
    "B4": dict(front=3.0, rear=6.0, side=3.0, upper=3.0, storeys=6, label="Mixed Use"),
    "MU1": dict(front=3.0, rear=6.0, side=3.0, upper=3.0, storeys=6, label="Mixed Use"),
    "E1": dict(front=3.0, rear=6.0, side=3.0, upper=3.0, storeys=4, label="Local Centre"),
    "E2": dict(front=3.0, rear=6.0, side=3.0, upper=3.0, storeys=6, label="Commercial Centre"),
}
DEFAULT_PARAMS = dict(front=5.4, rear=4.8, side=3.0, upper=3.0, storeys=4, label="Unknown / default")


# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------
def _get(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _arcgis_point(service, lon, lat, out_fields="*"):
    """Point-intersect query against an ArcGIS feature/map-server layer -> GeoJSON."""
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "true",
        "f": "geojson",
    }
    return _get(service + "/query", params)


def _arcgis_envelope(service, lon, lat, half_deg, out_fields="*", max_rec=200):
    """Bounding-box query -> GeoJSON (used for surrounding context parcels)."""
    xmin, ymin = lon - half_deg, lat - half_deg
    xmax, ymax = lon + half_deg, lat + half_deg
    params = {
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "true",
        "resultRecordCount": max_rec,
        "f": "geojson",
    }
    return _get(service + "/query", params)


def _first_feature(geojson):
    feats = (geojson or {}).get("features") or []
    return feats[0] if feats else None


# ----------------------------------------------------------------------------
# Geometry: local projection, area, PCA-oriented bounding box
# ----------------------------------------------------------------------------
def _outer_ring(feature):
    """Return the outer ring [[lon,lat],...] of a (Multi)Polygon GeoJSON feature."""
    g = feature.get("geometry") or {}
    t, c = g.get("type"), g.get("coordinates")
    if not c:
        return []
    if t == "Polygon":
        return c[0]
    if t == "MultiPolygon":
        # largest part by vertex count is a good-enough proxy for the main lot
        return max(c, key=lambda poly: len(poly[0]))[0]
    return []


def _to_local_m(ring, lat0, lon0):
    """Equirectangular projection to metres, centred at (lat0, lon0)."""
    k = math.cos(math.radians(lat0))
    return [[(lon - lon0) * 111320.0 * k, (lat - lat0) * 110540.0] for lon, lat in ring]


def _area_m2(pts):
    a = 0.0
    n = len(pts)
    for i in range(n - 1):
        a += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return abs(a) / 2.0


def _centroid_lonlat(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _convex_hull(pts):
    """Andrew's monotone chain. Returns hull vertices CCW (no repeated last point)."""
    p = sorted(set((round(x, 4), round(y, 4)) for x, y in pts))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for q in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    upper = []
    for q in reversed(p):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _min_area_bbox(pts):
    """Minimum-area oriented bounding box via rotating calipers over hull edges.
    Returns (length, depth, angle_rad) with length >= depth."""
    hull = _convex_hull(pts)
    if len(hull) < 3:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return max(xs) - min(xs), max(ys) - min(ys), 0.0
    best = None
    n = len(hull)
    for i in range(n):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % n]
        edge = math.hypot(bx - ax, by - ay)
        if edge < 1e-9:
            continue
        ux, uy = (bx - ax) / edge, (by - ay) / edge  # edge direction
        us = [(x - ax) * ux + (y - ay) * uy for x, y in hull]
        vs = [-(x - ax) * uy + (y - ay) * ux for x, y in hull]
        w = max(us) - min(us)
        h = max(vs) - min(vs)
        area = w * h
        ang = math.atan2(uy, ux)
        if best is None or area < best[0]:
            best = (area, w, h, ang)
    _, w, h, ang = best
    if h > w:
        w, h = h, w
        ang += math.pi / 2
    return w, h, ang


def _parcel_metrics(feature):
    ring = _outer_ring(feature)
    if len(ring) < 4:
        return None
    lon0, lat0 = _centroid_lonlat(ring)
    local = _to_local_m(ring, lat0, lon0)
    length, depth, angle = _min_area_bbox(local)
    return {
        "area_sqm": round(_area_m2(local), 1),
        "frontage_m": round(length, 2),
        "depth_m": round(depth, 2),
        "orientation_deg": round(math.degrees(angle), 2),
        "centroid": [lon0, lat0],
    }


# ----------------------------------------------------------------------------
# Geocoding (keyless, AU-filtered). Swap for a keyed provider in production.
# ----------------------------------------------------------------------------
def geocode(q):
    data = _get(NOMINATIM, {
        "q": q, "format": "jsonv2", "countrycodes": "au",
        "limit": 1, "addressdetails": 1,
    })
    if not data:
        return None
    top = data[0]
    state = (top.get("address") or {}).get("state", "")
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "label": top.get("display_name", q),
        "state": "VIC" if "Victoria" in state else ("NSW" if "New South Wales" in state else state),
    }


def _detect_state(lat, lon, hint=None):
    if hint in ("VIC", "NSW"):
        return hint
    # VIC roughly south of -34 lat; otherwise probe VIC zone service.
    try:
        if _first_feature(_arcgis_point(VIC_ZONE, lon, lat, out_fields="ZONE_CODE")):
            return "VIC"
    except Exception:
        pass
    return "NSW"


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@site_bp.route("/api/site/geocode")
def route_geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(error="missing q"), 400
    try:
        g = geocode(q)
    except Exception as e:
        return jsonify(error=f"geocode failed: {e}"), 502
    if not g:
        return jsonify(error="no match"), 404
    return jsonify(g)


@site_bp.route("/api/site/parcel")
def route_parcel():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        return jsonify(error="missing lat/lon"), 400
    state = _detect_state(lat, lon, request.args.get("state"))
    service = VIC_PARCEL if state == "VIC" else NSW_CADASTRE_LOT
    try:
        gj = _arcgis_point(service, lon, lat)
    except Exception as e:
        return jsonify(error=f"parcel lookup failed: {e}"), 502
    feat = _first_feature(gj)
    if not feat:
        return jsonify(error="no parcel at point", state=state), 404
    out = {"state": state, "parcel": feat, "metrics": _parcel_metrics(feat)}
    if request.args.get("context"):
        try:
            out["context"] = _context_features(service, lon, lat, 120)
        except Exception:
            out["context"] = {"type": "FeatureCollection", "features": []}
    return jsonify(out)


@site_bp.route("/api/site/zone")
def route_zone():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    if lat is None or lon is None:
        return jsonify(error="missing lat/lon"), 400
    state = _detect_state(lat, lon, request.args.get("state"))
    return jsonify(_zone_payload(state, lat, lon))


@site_bp.route("/api/site/context")
def route_context():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    radius = request.args.get("radius", default=120, type=float)
    if lat is None or lon is None:
        return jsonify(error="missing lat/lon"), 400
    state = _detect_state(lat, lon, request.args.get("state"))
    service = VIC_PARCEL if state == "VIC" else NSW_CADASTRE_LOT
    try:
        return jsonify(_context_features(service, lon, lat, radius))
    except Exception as e:
        return jsonify(error=f"context failed: {e}"), 502


@site_bp.route("/api/site/resolve")
def route_resolve():
    """One-shot convenience: address -> everything the simulator needs."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(error="missing q"), 400
    try:
        g = geocode(q)
    except Exception as e:
        return jsonify(error=f"geocode failed: {e}"), 502
    if not g:
        return jsonify(error="no match"), 404
    lat, lon = g["lat"], g["lon"]
    state = _detect_state(lat, lon, g.get("state"))
    service = VIC_PARCEL if state == "VIC" else NSW_CADASTRE_LOT
    resp = {"geocode": g, "state": state}
    try:
        feat = _first_feature(_arcgis_point(service, lon, lat))
        resp["parcel"] = feat
        resp["metrics"] = _parcel_metrics(feat) if feat else None
    except Exception as e:
        resp["parcel_error"] = str(e)
    try:
        resp["zone"] = _zone_payload(state, lat, lon)
    except Exception as e:
        resp["zone_error"] = str(e)
    try:
        resp["context"] = _context_features(service, lon, lat, 120)
    except Exception:
        resp["context"] = {"type": "FeatureCollection", "features": []}
    return jsonify(resp)


# ----------------------------------------------------------------------------
# Zone + context builders
# ----------------------------------------------------------------------------
def _zone_payload(state, lat, lon):
    code, desc = None, None
    fsr, height, storeys_from_height = None, None, None
    if state == "VIC":
        f = _first_feature(_arcgis_point(VIC_ZONE, lon, lat))
        if f:
            p = f.get("properties", {})
            code = (p.get("ZONE_CODE") or p.get("zone_code") or "").upper()
            desc = p.get("ZONE_DESCRIPTION") or p.get("LGA") or None
    else:
        f = _first_feature(_arcgis_point(NSW_ZONE, lon, lat))
        if f:
            p = f.get("properties", {})
            code = (p.get("SYM_CODE") or "").upper()
            desc = p.get("LAY_CLASS") or p.get("EPI_NAME")
        # NSW publishes FSR + height-of-building as their own layers
        try:
            ff = _first_feature(_arcgis_point(NSW_FSR, lon, lat))
            if ff:
                fsr = ff.get("properties", {}).get("FSR")
        except Exception:
            pass
        try:
            fh = _first_feature(_arcgis_point(NSW_HOB, lon, lat))
            if fh:
                height = fh.get("properties", {}).get("MAX_B_H") or fh.get("properties", {}).get("HOB")
        except Exception:
            pass
    if height:
        try:
            storeys_from_height = max(1, int(float(height) // FLOOR_TO_FLOOR_M))
        except (TypeError, ValueError):
            storeys_from_height = None

    key = (code or "").split("(")[0].strip()
    params = dict(ZONE_DEFAULTS.get(key, DEFAULT_PARAMS))
    if storeys_from_height:
        params["storeys"] = min(6, storeys_from_height)  # mid-rise cap
    return {
        "state": state,
        "zone": code,
        "zone_label": params.pop("label", desc),
        "zone_description": desc,
        "fsr": fsr,
        "height_limit_m": height,
        "setbacks": {k: params[k] for k in ("front", "rear", "side", "upper")},
        "suggested_storeys": params["storeys"],
        "disclaimer": "Defaults keyed to zone. Binding controls are in the DCP / planning scheme.",
    }


def _context_features(service, lon, lat, radius_m):
    half_deg = (radius_m / 111320.0)
    gj = _arcgis_envelope(service, lon, lat, half_deg)
    # strip attributes to keep the payload light for the browser
    feats = []
    for f in (gj.get("features") or []):
        feats.append({"type": "Feature", "properties": {}, "geometry": f.get("geometry")})
    return {"type": "FeatureCollection", "features": feats}


if __name__ == "__main__":
    # quick local smoke test: python app_site_routes.py "200 Spencer St, Melbourne"
    import sys
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(site_bp)
    if len(sys.argv) > 1:
        with app.test_client() as c:
            r = c.get("/api/site/resolve", query_string={"q": sys.argv[1]})
            print(json.dumps(r.get_json(), indent=2)[:4000])
    else:
        app.run(debug=True, port=5050)