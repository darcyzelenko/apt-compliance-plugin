"""
Victorian Apartment Compliance Checker  v0.4
Flask web application
"""
from flask import Flask, request, jsonify, render_template
import os, sys, json, urllib.request, urllib.parse, urllib.error

sys.path.insert(0, os.path.dirname(__file__))
from compliance_engine import run_compliance

app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB


@app.route('/')
def index():
    return render_template('index.html')


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
    if jurisdiction not in ('VIC','NSW','BEST_PRACTICE'):
        jurisdiction = 'VIC'
    try:
        results = run_compliance(text, ceiling_h=ceiling_h, jurisdiction=jurisdiction)
    except Exception as e:
        import traceback
        return jsonify({'error': f'Compliance check failed: {e}', 'trace': traceback.format_exc()}), 500
    return jsonify(results)


@app.route('/api/geocode', methods=['GET'])
def geocode():
    """
    Geocode an address using Nominatim (OSM) — free, no API key needed.
    Returns lat/lon + identified noise sources within influence distances.
    GET /api/geocode?address=123+Smith+St+Collingwood+VIC
    """
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({'error': 'No address provided'}), 400

    # Nominatim geocode
    encoded = urllib.parse.quote(address + ', Victoria, Australia')
    url = f'https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=1&countrycodes=au'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'VIC-Apartment-Compliance-Checker/0.4 (academic research)'
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

    # Query Overpass API for nearby noise sources
    noise_sources = query_noise_sources(lat, lon)

    return jsonify({
        'address': display,
        'lat': lat,
        'lon': lon,
        'noise_sources': noise_sources,
    })


def query_noise_sources(lat, lon):
    """
    Use Overpass API to find railways and major roads within noise influence distances.
    Table D3: industry 300m, roads 300m, rail 80-135m.
    """
    # Search radius — use 400m to catch anything near influence boundaries
    radius = 400

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
        'User-Agent': 'VIC-Apartment-Compliance-Checker/0.4'
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
            a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

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
                # Determine freight vs passenger
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



@app.route('/trace')
def trace():
    return render_template('tracer.html')


@app.route('/api/fetch-floorplan', methods=['POST'])
def fetch_floorplan():
    """
    Server-side proxy to fetch floor plan images from listing sites.
    Avoids CORS issues and lets us parse listing pages for image URLs.
    POST { "url": "https://www.realestate.com.au/property/..." }
    Returns { "image_url": "...", "images": [...], "address": "..." }
    """
    body = request.get_json(silent=True) or {}
    url = body.get('url', '').strip()
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    # Validate — only allow known listing domains
    allowed = ['realestate.com.au', 'domain.com.au', 'allhomes.com.au']
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace('www.', '')
    if not any(host.endswith(d) for d in allowed):
        return jsonify({'error': f'Only realestate.com.au, domain.com.au and allhomes.com.au are supported'}), 400

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-AU,en;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return jsonify({'error': f'Could not fetch listing: {e}'}), 502

    import re

    # Extract floor plan images — look for common patterns
    floor_plan_images = []

    # realestate.com.au: floor plan images are in JSON-LD or og tags
    # Pattern: "floorplan" near image URL
    patterns = [
        r'https?://[^"\']+(?:floorplan|floor.plan|fp)[^"\']*\.(?:jpg|jpeg|png|webp)',
        r'https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*(?:floorplan|floor.plan)',
        # realestate.com.au CDN pattern
        r'https?://rimages\.realestate\.com\.au/[^"\']+\.(?:jpg|jpeg|png)',
        # domain.com.au
        r'https?://bucket-[^"\']+\.domain\.com\.au/[^"\']+\.(?:jpg|jpeg|png)',
    ]

    seen = set()
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            img = m.group(0).split('"')[0].split("'")[0]
            if img not in seen:
                seen.add(img)
                floor_plan_images.append(img)

    # Also grab all images labelled near "floor" text in JSON blobs
    json_blobs = re.findall(r'\{[^{}]{0,2000}floor[^{}]{0,2000}\}', html, re.IGNORECASE | re.DOTALL)
    for blob in json_blobs[:10]:
        for m in re.finditer(r'"(?:url|src|href)"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', blob, re.IGNORECASE):
            img = m.group(1)
            if img not in seen:
                seen.add(img)
                floor_plan_images.append(img)

    # Extract address
    address = ''
    addr_patterns = [
        r'<title>([^<]+)</title>',
        r'"streetAddress"\s*:\s*"([^"]+)"',
        r'"address"\s*:\s*"([^"]+)"',
        r'property-info-address[^>]*>([^<]+)<',
    ]
    for pat in addr_patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) > 5 and len(candidate) < 200:
                address = candidate
                break

    if not floor_plan_images:
        return jsonify({
            'error': 'No floor plan images found on this listing. Try pasting the floor plan image URL directly.',
            'images': [],
            'address': address,
        }), 404

    return jsonify({
        'image_url': floor_plan_images[0],
        'images': floor_plan_images[:6],
        'address': address,
    })


@app.route('/api/proxy-image')
def proxy_image():
    """
    Proxy an image URL to avoid CORS when loading listing images into canvas.
    GET /api/proxy-image?url=https://...
    """
    url = request.args.get('url', '').strip()
    if not url:
        return 'No URL', 400
    if not url.startswith('http'):
        return 'Invalid URL', 400

    import re
    # Strip CDN filter directives that force webp — serve jpeg/png instead
    clean_url = re.sub(r'filters:[^/]+/', '', url)

    for attempt_url in ([clean_url, url] if clean_url != url else [url]):
        try:
            req = urllib.request.Request(attempt_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
                'Referer': 'https://www.domain.com.au/',
                'Accept': 'image/png,image/jpeg,image/gif,image/webp,image/*,*/*',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                ct = resp.headers.get('Content-Type', 'image/jpeg')
            from flask import Response
            return Response(data, content_type=ct)
        except Exception:
            continue
    return 'Could not fetch image', 502

import uuid
import time
import json
import os
import threading

# File-backed session store -- survives server restarts within same dyno
# Falls back gracefully if filesystem not writable
SESSION_TTL = 7200  # 2 hours
_SESSION_FILE = '/tmp/apt_sessions.json'
_session_lock = threading.Lock()

def _load_sessions():
    try:
        if os.path.exists(_SESSION_FILE):
            with open(_SESSION_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_sessions(sessions):
    try:
        with open(_SESSION_FILE, 'w') as f:
            json.dump(sessions, f)
    except Exception:
        pass

def _clean_and_load():
    with _session_lock:
        sessions = _load_sessions()
        now = time.time()
        sessions = {k: v for k, v in sessions.items() if now - v['ts'] < SESSION_TTL}
        _save_sessions(sessions)
    return sessions

def _get_session(token):
    with _session_lock:
        sessions = _load_sessions()
        s = sessions.get(token)
        if s and time.time() - s['ts'] < SESSION_TTL:
            return s
        return None

def _set_session(token, results, ts=None):
    with _session_lock:
        sessions = _load_sessions()
        entry = {'results': results, 'ts': ts or time.time()}
        sessions[token] = entry
        _save_sessions(sessions)
        return entry


@app.route('/api/store', methods=['POST'])
def store_results():
    """
    Accepts a DXF file, runs compliance check, stores results, returns a token.
    Used by Rhino/Revit plugins to get a shareable report URL.
    POST multipart: dxf_file, ceiling_height, jurisdiction
    Returns: { token: "abc123", url: "https://.../report/abc123" }
    """
    try:
        if 'dxf_file' not in request.files:
            return jsonify({'error': 'No DXF file'}), 400

        f = request.files['dxf_file']
        text = f.read().decode('utf-8', errors='replace')
        ceiling_h = float(request.form.get('ceiling_height', 2.7))
        jurisdiction = request.form.get('jurisdiction', 'VIC').upper()
        if jurisdiction not in ('VIC', 'NSW', 'BEST_PRACTICE'):
            jurisdiction = 'VIC'
    except Exception as e:
        import traceback
        return jsonify({'error': 'Request parsing failed: ' + str(e), 'traceback': traceback.format_exc()}), 500

    try:
        results = run_compliance(text, ceiling_h=ceiling_h, jurisdiction=jurisdiction)
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500

    try:
        token = uuid.uuid4().hex[:12]
        _set_session(token, results)
    except Exception as e:
        import traceback
        return jsonify({'error': 'Session storage failed: ' + str(e), 'traceback': traceback.format_exc()}), 500

    base_url = request.host_url.rstrip('/')
    return jsonify({
        'token': token,
        'url': f'{base_url}/report/{token}',
    })


@app.route('/report/<token>')
def report(token):
    """Serve the main page -- JS will fetch results using the token."""
    return render_template('index.html')


@app.route('/api/test')
def api_test():
    """Quick health check -- returns engine version info."""
    try:
        from compliance_engine import build_adjacency
        import inspect
        sig = str(inspect.signature(build_adjacency))
        return jsonify({'ok': True, 'build_adjacency_sig': sig, 'version': '2.0'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/about')
@app.route('/about/')
def about():
    return render_template('marketing.html')


@app.route('/standards')
@app.route('/standards/')
def standards():
    return render_template('standards.html')


@app.route('/api/results/<token>')
def get_results(token):
    session = _get_session(token)
    if not session:
        return jsonify({'error': 'Session not found or expired'}), 404
    return jsonify({'results': session['results'], 'ts': session['ts']})


@app.route('/api/update/<token>', methods=['POST'])
def update_results(token):
    if not _get_session(token):
        return jsonify({'error': 'Session not found'}), 404
    if 'dxf_file' not in request.files:
        return jsonify({'error': 'No DXF file'}), 400
    f = request.files['dxf_file']
    text = f.read().decode('utf-8', errors='replace')
    ceiling_h = float(request.form.get('ceiling_height', 2.7))
    jurisdiction = request.form.get('jurisdiction', 'VIC').upper()
    try:
        results = run_compliance(text, ceiling_h=ceiling_h, jurisdiction=jurisdiction)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    ts_now = time.time()
    _set_session(token, results, ts_now)
    return jsonify({'ok': True, 'ts': ts_now})


@app.route('/api/analyse-image', methods=['POST'])
def analyse_image():
    """
    Send a floor plan image to Claude vision API.
    Returns structured room data: polygons, scale, north.
    POST JSON: { image: "<base64>", media_type: "image/jpeg" }
    """
    import os, requests as req

    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured on server'}), 500

    body = request.get_json()
    if not body or 'image' not in body:
        return jsonify({'error': 'No image provided'}), 400

    image_data = body['image']
    media_type = body.get('media_type', 'image/jpeg')

    prompt = """Analyse this apartment floor plan image and extract room geometry.

Return ONLY a JSON object in this exact format, no other text:
{
  "rooms": [
    {
      "layer": "APT_ROOM_LIVING",
      "label": "Living",
      "vertices": [[0.12, 0.15], [0.45, 0.15], [0.45, 0.48], [0.12, 0.48]]
    }
  ],
  "scale": {
    "found": true,
    "pixels_per_metre": null,
    "dimension_text": "3200",
    "dimension_metres": 3.2,
    "note": "Found '3200' label on bedroom"
  },
  "north": {
    "found": false,
    "direction_degrees": 0
  }
}

RULES:
- vertices are [x, y] as proportions of image width and height (0.0 to 1.0), origin top-left
- trace the actual room boundaries as accurately as possible, use 4-8 vertices per room
- do not overlap rooms - each area belongs to one room only
- layer must be one of: APT_ROOM_MAINBED, APT_ROOM_BED1, APT_ROOM_BED2, APT_ROOM_BED3, APT_ROOM_LIVING, APT_ROOM_BATHROOM, APT_ROOM_ENSUITE, APT_ROOM_LAUNDRY, APT_ROOM_ENTRY, APT_STORAGE_DESIGNATED, APT_POS
- identify balconies/outdoor areas as APT_POS
- identify combined bathroom+toilet as APT_ROOM_BATHROOM, separate toilet as APT_ROOM_BATHROOM
- identify WIR/wardrobe as APT_STORAGE_DESIGNATED
- if you see a dimension annotation (e.g. "3200" or "3.2m"), report it in scale
- north: 0=up, 90=right, 180=down, 270=left
- if the plan is too small or unclear, still return your best attempt
- ONLY return the JSON object, nothing else"""

    try:
        response = req.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-opus-4-5',
                'max_tokens': 2000,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': image_data,
                            }
                        },
                        {
                            'type': 'text',
                            'text': prompt
                        }
                    ]
                }]
            },
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        text = data['content'][0]['text'].strip()

        # Strip markdown fences if present
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        text = text.strip()

        import json as json_mod
        result = json_mod.loads(text)
        return jsonify(result)

    except req.exceptions.RequestException as e:
        return jsonify({'error': 'API request failed: ' + str(e)}), 502
    except Exception as e:
        return jsonify({'error': 'Analysis failed: ' + str(e)}), 500



if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print(f'\n  Apt. Compliance Checker — http://localhost:{port}\n')
    app.run(debug=debug, host='0.0.0.0', port=port)