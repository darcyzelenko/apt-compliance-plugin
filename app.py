"""
Victorian Apartment Compliance Checker  v0.5
Flask web application
"""
from flask import Flask, request, jsonify, render_template, send_file
import os, sys, json, urllib.request, urllib.parse, urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from compliance_engine import run_compliance

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/massing-simulator')
def massing_simulator():
    return send_file('templates/massing-simulator.html')


@app.route('/tracer')
def tracer():
    return send_file(os.path.join(os.path.dirname(__file__), 'tracer.html'))


# ----------------------------------------------------------------------------
# AI floor-pla n tracer 
# ----------------------------------------------------------------------------

# Shared room-type vocabulary used across the seed/merge prompts
LAYER_VOCAB = (
    "layer is one of: APT_ROOM_MAINBED, APT_ROOM_BED1, APT_ROOM_BED2, "
    "APT_ROOM_BED3, APT_ROOM_LIVING, APT_ROOM_BATHROOM, APT_ROOM_ENSUITE, "
    "APT_ROOM_LAUNDRY, APT_ROOM_ENTRY, APT_STORAGE_DESIGNATED, APT_POS, "
    "APT_UNKNOWN. Balcony/outdoor/POS = APT_POS; bath/toilet/combined = "
    "APT_ROOM_BATHROOM; WIR/robe/BIR = APT_STORAGE_DESIGNATED."
)

COMMON = (
    "Coordinates are [x,y] as fractions of the FULL image (0..1), origin "
    "top-left. Use clean rectilinear polygons with right angles. "
    "Output ONLY a JSON object inside a ```json code block, nothing else."
)


def _build_prompt(task, body, hint):
    """Return the text prompt for the requested tracer task."""
    if task == 'label_rooms':
        rooms_in = body.get('rooms', [])   # [{idx, bbox:[x1,y1,x2,y2]}]
        return f"""You are classifying rooms a user has already boxed on a floor plan.
The user drew these room rectangles (image fractions, [x1,y1,x2,y2]): {rooms_in}

For EACH rectangle, return its layer based on the room label visible inside or beside the box, or the fixtures shown if no label. Geometry is fixed by the user - do NOT return any coordinates, just the type.
{COMMON}
{{"rooms":[{{"idx":0,"layer":"APT_ROOM_BED1","label":"Bed 1"}}],
  "scale":{{"found":false}}, "north":{{"found":false,"direction_degrees":0}}}}
{LAYER_VOCAB}"""

    if task == 'fit_boundary':
        user_poly = body.get('boundary', [])
        return f"""You are refining an apartment's outer boundary on a floor plan.
The user roughly traced this boundary polygon (image fractions): {user_poly}
Snap it to the ACTUAL outer wall line in the image: correct mis-angled segments to true horizontals/verticals, fix corner positions, and add corners for any notch or wing the user missed. Keep it a single closed rectilinear polygon close to what the user drew - do not invent a totally different shape, and do not extend beyond the building's outer walls.
{COMMON}
{{"perimeter": [[x,y],[x,y], ...]}}"""

    if task == 'rooms_from_seeds':
        boundary = body.get('boundary', [])
        seeds = body.get('seeds', [])
        prev = body.get('previous')
        correction = f"\nApply this correction from the user: {hint}\nThese were the previous rooms: {prev}" if hint else ""
        return f"""You are an experienced residential architect tracing rooms on a floor plan.
The apartment boundary polygon (image fractions). Do NOT place anything outside it: {boundary}
The user dropped one point inside each room they want captured (image fractions), numbered in order: {seeds}

For EACH point, return exactly one room polygon:
- read the printed room label nearest that point to classify it; if there is no label, infer the type from the fixtures shown.
- trace the room's actual walls as a clean rectilinear polygon (rectangle = 4 points; L / "snorkel" shape = 6).
- rooms must stay inside the boundary, must not overlap, and where two rooms share a wall give them IDENTICAL coordinates along that wall so the edges line up exactly.
Return ONLY rooms (doors and windows are detected separately - do not include them).{correction}
{COMMON}
{{"rooms":[{{"layer":"APT_ROOM_BED1","label":"Bed 1","vertices":[[x,y],[x,y],[x,y],[x,y]]}}]}}
{LAYER_VOCAB}"""

    if task in ('doors', 'windows'):
        boundary = body.get('boundary', [])
        kind = task[:-1]  # 'door' or 'window'
        if kind == 'door':
            what = ("Find every DOORWAY: a gap in a wall, usually drawn with a quarter-circle "
                    "swing arc. Include entry doors, internal doors and sliding doors. Do NOT "
                    "return windows.")
        else:
            what = ("Find every WINDOW: a gap in an EXTERNAL wall, usually drawn as a thin double "
                    "line or a break in the outer wall, often along the balcony or outer edges. Do "
                    "NOT return doors.")
        return f"""You are an architect reading openings on a floor plan.
The apartment boundary (image fractions), stay within it: {boundary}
{what}
Return each opening as a short [start,end] segment lying along the wall line it sits in.
{COMMON}
{{"openings":[{{"type":"{kind}","segment":[[x,y],[x,y]]}}]}}"""

    if task == 'merge':
        rooms_in = body.get('rooms', [])
        return f"""You are an architect cleaning up a traced room layout.
Here are the traced rooms (image fractions): {rooms_in}

Look at the image and MERGE rooms that are connected with NO door or threshold between them, by unioning their polygons into a single rectilinear polygon:
- living + kitchen + dining + entry that flow together with no dividing walls -> one APT_ROOM_LIVING.
- a bedroom and its adjoining WIR / BIR / robe -> one bedroom polygon.
- a bathroom and an adjoining laundry with no door between them -> one APT_ROOM_BATHROOM.
Leave genuinely separate rooms (anything with a door or full wall between them) unchanged. Return the FULL updated room list (merged spaces plus the rooms you left alone).
{COMMON}
{{"rooms":[{{"layer":"APT_ROOM_LIVING","label":"Living","vertices":[[x,y], ...]}}]}}
{LAYER_VOCAB}"""

    # ---- 'full' fallback: one-shot perimeter + rooms (no user input) ----
    prompt = """You are an experienced residential architect extracting clean geometry from an apartment floor plan.

Work in three steps.

STEP 1 - PLACE & TRACE THE PERIMETER.
Find the apartment's outer wall outline (thick perimeter). It is usually a RECTILINEAR polygon - a rectangle with bits added or notched out, all right angles. Ignore margins, title block, area schedule, legend, north symbol, dimension strings, and any car space marked "Not To Scale"/"Not In Position".
- "apartment_bounds": tight bounding box of that outline, as fractions of the FULL image {"x":left,"y":top,"w":width,"h":height}.
- "perimeter": the outline as a polygon of [x,y] points in APARTMENT-RELATIVE coords (0..1 within apartment_bounds). Use only right angles; add points for every notch/wing.

STEP 2 - PLACE THE RECTANGULAR ROOMS FIRST (subtractive method).
In this order, place the rooms that are almost always simple rectangles (occasionally an L / "snorkel" shape): (1) bedrooms, (2) bathrooms/ensuites, (3) balconies, (4) laundry/storage/kitchen if walled off. Trace each tightly to its walls.

STEP 3 - REMAINDER IS LIVING.
Whatever interior area is left after subtracting those rooms is the living space (living/dining/meals/entry/circulation) - return it as ONE APT_ROOM_LIVING polygon (it may be an L or U shape; use as many right-angle vertices as needed). If any leftover area is genuinely unclear, return it as APT_UNKNOWN rather than leaving a gap.

Output a single JSON object inside a ```json code block:
{
  "apartment_bounds": {"x":0.22,"y":0.15,"w":0.30,"h":0.55},
  "perimeter": [[0,0],[1,0],[1,0.7],[0.6,0.7],[0.6,1],[0,1]],
  "rooms": [
    {"id":"r1","layer":"APT_ROOM_BED1","label":"Bed 1","dimensions":"3.0 x 2.7","vertices":[[0.0,0.0],[0.5,0.0],[0.5,0.45],[0.0,0.45]],"adjacent_to":["r2"]}
  ],
  "openings": [
    {"type":"door","segment":[[0.3,0.45],[0.4,0.45]]},
    {"type":"window","segment":[[0,0.1],[0.3,0.1]]}
  ],
  "scale": {"found": false, "dimension_text": null, "dimension_metres": null, "note": ""},
  "north": {"found": false, "direction_degrees": 0}
}

RULES:
- ALL room/opening/perimeter coords are APARTMENT-RELATIVE (0..1 within apartment_bounds), origin top-left.
- clean rectilinear polygons, right angles only. Rectangles = 4 points; L/snorkel = 6.
- rooms must not overlap; where two rooms share a wall, give them the SAME coordinates along that wall so the edges line up exactly.
- rooms together with the living remainder should fill the whole perimeter - no gaps.
- """ + LAYER_VOCAB + """
- "adjacent_to": ids of rooms sharing a wall. "dimensions": printed size label if visible, else null.
- door = wall gap with swing arc; window = gap in external wall. Each as a short [start,end] segment. Empty list if unsure.

COVERAGE (for long/narrow plans):
- If the plan is tall and narrow, scan top to bottom and capture EVERY labelled room, including ones at the very top and very bottom. Do not cluster rooms in the centre and skip the ends."""
    if hint:
        prompt += "\n\nUSER CORRECTION (apply this exactly): " + hint
    return prompt


@app.route('/api/analyse-image', methods=['POST'])
def analyse_image():
    """
    Floor plan -> structured geometry via Claude vision (urllib, no requests dep).
    POST JSON: { image, media_type, task?, boundary?, seeds?, rooms?, hint? }
      task = 'fit_boundary' | 'rooms_from_seeds' | 'merge' | 'full' (default)
    """
    import re
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured on server'}), 500

    body = request.get_json(silent=True) or {}
    if 'image' not in body:
        return jsonify({'error': 'No image provided'}), 400

    image_data = body['image']
    media_type = body.get('media_type', 'image/jpeg')
    task = body.get('task', 'full')
    hint = (body.get('hint') or '').strip()

    prompt = _build_prompt(task, body, hint)

    payload = json.dumps({
        'model': 'claude-sonnet-4-5',
        'max_tokens': 3000,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': image_data}},
                {'type': 'text', 'text': prompt}
            ]
        }]
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=payload,
        headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return jsonify({'error': f'Claude API {e.code}: {e.read().decode("utf-8", "replace")[:300]}'}), 502
    except Exception as e:
        return jsonify({'error': 'Request failed: ' + str(e)}), 502

    try:
        text = data['content'][0]['text']
        m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            s, e = text.find('{'), text.rfind('}')
            text = text[s:e + 1]
        return jsonify(json.loads(text))
    except Exception as e:
        return jsonify({'error': 'Could not parse Claude response: ' + str(e)}), 500


@app.route('/whoami')
def whoami():
    root = os.path.dirname(__file__)
    return jsonify({
        'files_at_root': os.listdir(root),
        'tracer_exists': os.path.exists(os.path.join(root, 'tracer.html')),
        'routes': [str(r) for r in app.url_map.iter_rules()],
    })


# ----------------------------------------------------------------------------
# Compliance checking
# ----------------------------------------------------------------------------

@app.route('/api/check', methods=['POST'])
def check_compliance():
    if 'dxf_file' not in request.files:
        return jsonify({'error': 'No DXF file uploaded'}), 400
    f = request.files['dxf_file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400
    try:
        ceiling_h = float(request.form.get('ceiling_height', 2.7))
        ceiling_h = max(2.1, min(5.0, ceiling_h))
    except (ValueError, TypeError):
        ceiling_h = 2.7
    try:
        text = f.read().decode('utf-8', errors='replace')
    except Exception as e:
        return jsonify({'error': f'Could not read file: {e}'}), 400
    jurisdiction = request.form.get('jurisdiction', 'VIC').strip().upper()
    if jurisdiction not in ('VIC', 'NSW', 'BEST_PRACTICE'):
        jurisdiction = 'VIC'
    try:
        results = run_compliance(text, ceiling_h=ceiling_h, jurisdiction=jurisdiction)
    except Exception as e:
        import traceback
        return jsonify({'error': f'Compliance check failed: {e}', 'trace': traceback.format_exc()}), 500
    return jsonify(results)


# ----------------------------------------------------------------------------
# Site / acoustic context lookup
# ----------------------------------------------------------------------------

@app.route('/api/geocode', methods=['GET'])
def geocode():
    """
    Geocode an address using Nominatim (OSM) and return nearby noise sources.
    GET /api/geocode?address=123+Smith+St+Collingwood+VIC
    """
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({'error': 'No address provided'}), 400

    encoded = urllib.parse.quote(address + ', Victoria, Australia')
    url = f'https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=1&countrycodes=au'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'VIC-Apartment-Compliance-Checker/0.5 (academic research)'
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return jsonify({'error': f'Geocoding failed: {e}'}), 502

    if not data:
        return jsonify({'error': 'Address not found. Try adding suburb and state (e.g. Collingwood VIC)'}), 404

    lat = float(data[0]['lat'])
    lon = float(data[0]['lon'])
    display = data[0].get('display_name', address)
    noise_sources = query_noise_sources(lat, lon)

    return jsonify({'address': display, 'lat': lat, 'lon': lon, 'noise_sources': noise_sources})


def query_noise_sources(lat, lon):
    """
    Use Overpass API to find railways and major roads within noise influence distances.
    Table D3: industry 300m, roads 300m, rail 80-135m.
    """
    radius = 400  # catch anything near the influence boundaries
    overpass_query = f"""
[out:json][timeout:10];
(
  way["railway"~"rail|subway|tram"](around:{radius},{lat},{lon});
  way["highway"~"motorway|trunk|primary"](around:{radius},{lat},{lon});
  way["landuse"="industrial"](around:{radius},{lat},{lon});
  relation["landuse"="industrial"](around:{radius},{lat},{lon});
);
out center tags;
"""
    url = 'https://overpass-api.de/api/interpreter'
    data = urllib.parse.urlencode({'data': overpass_query}).encode()
    req = urllib.request.Request(url, data=data, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'VIC-Apartment-Compliance-Checker/0.5'
    })

    sources = []
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            result = json.loads(resp.read())

        import math
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            φ1, φ2 = math.radians(lat1), math.radians(lat2)
            Δφ = math.radians(lat2 - lat1)
            Δλ = math.radians(lon2 - lon1)
            a = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        for el in result.get('elements', []):
            tags = el.get('tags', {})
            center = el.get('center') or {}
            elat = center.get('lat') or el.get('lat') or lat
            elon = center.get('lon') or el.get('lon') or lon
            dist = round(haversine(lat, lon, elat, elon))

            railway = tags.get('railway', '')
            highway = tags.get('highway', '')
            landuse = tags.get('landuse', '')
            name = tags.get('name', '')

            if railway in ('rail', 'subway'):
                service = tags.get('service', '')
                freight = 'freight' in name.lower() or service == 'freight'
                source_type = 'RAIL_FREIGHT_METRO' if freight else 'RAIL_PASSENGER'
                influence = 135 if freight else 80
                sources.append({
                    'type': source_type,
                    'label': f"{'Freight' if freight else 'Passenger'} railway{f' ({name})' if name else ''}",
                    'distance_m': dist,
                    'influence_m': influence,
                    'in_range': dist <= influence,
                    'layer_suggestion': f'APT_NOISE_{source_type}',
                })
            elif railway == 'tram':
                pass  # trams not in Table D3
            elif highway in ('motorway', 'trunk', 'primary'):
                ref = tags.get('ref', '')
                label = name or ref or highway
                sources.append({
                    'type': 'ROAD',
                    'label': f"Major road{f' ({label})' if label else ''}",
                    'distance_m': dist,
                    'influence_m': 300,
                    'in_range': dist <= 300,
                    'layer_suggestion': 'APT_NOISE_ROAD',
                })
            elif landuse == 'industrial':
                sources.append({
                    'type': 'INDUSTRY',
                    'label': f"Industrial area{f' ({name})' if name else ''}",
                    'distance_m': dist,
                    'influence_m': 300,
                    'in_range': dist <= 300,
                    'layer_suggestion': 'APT_NOISE_INDUSTRY',
                })

        # Deduplicate by type — keep closest
        seen = {}
        for s in sorted(sources, key=lambda x: x['distance_m']):
            if s['type'] not in seen:
                seen[s['type']] = s
        sources = list(seen.values())

    except Exception as e:
        sources = [{'error': str(e), 'type': 'LOOKUP_FAILED'}]

    return sources


# ----------------------------------------------------------------------------
# Sample files
# ----------------------------------------------------------------------------

def _send_dxf(filename, download_name):
    path = os.path.join(os.path.dirname(__file__), filename)
    if not os.path.exists(path):
        return jsonify({'error': 'Sample not found'}), 404
    with open(path) as f:
        content = f.read()
    return content, 200, {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': f'attachment; filename="{download_name}"'
    }


@app.route('/api/sample')
def sample():
    return _send_dxf('test_apartment.dxf', 'sample_2bed_passing.dxf')


@app.route('/api/sample_fail')
def sample_fail():
    return _send_dxf('test_apartment_failing.dxf', 'sample_3bed_failing.dxf')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print(f'\n  Apt. Compliance Checker — http://localhost:{port}\n')
    app.run(debug=debug, host='0.0.0.0', port=port)