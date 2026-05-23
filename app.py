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

# In-memory session store — results live for 15 minutes
_sessions = {}
SESSION_TTL = 900  # seconds

def _clean_sessions():
    now = time.time()
    expired = [k for k, v in _sessions.items() if now - v['ts'] > SESSION_TTL]
    for k in expired:
        del _sessions[k]


@app.route('/api/store', methods=['POST'])
def store_results():
    """
    Accepts a DXF file, runs compliance check, stores results, returns a token.
    Used by Rhino/Revit plugins to get a shareable report URL.
    POST multipart: dxf_file, ceiling_height, jurisdiction
    Returns: { token: "abc123", url: "https://.../report/abc123" }
    """
    _clean_sessions()

    if 'dxf_file' not in request.files:
        return jsonify({'error': 'No DXF file'}), 400

    f = request.files['dxf_file']
    text = f.read().decode('utf-8', errors='replace')
    ceiling_h = float(request.form.get('ceiling_height', 2.7))
    jurisdiction = request.form.get('jurisdiction', 'VIC').upper()
    if jurisdiction not in ('VIC', 'NSW', 'BEST_PRACTICE'):
        jurisdiction = 'VIC'

    try:
        results = run_compliance(text, ceiling_h=ceiling_h, jurisdiction=jurisdiction)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    token = uuid.uuid4().hex[:12]
    _sessions[token] = {'results': results, 'ts': time.time()}

    base_url = request.host_url.rstrip('/')
    return jsonify({
        'token': token,
        'url': f'{base_url}/report/{token}',
    })


@app.route('/report/<token>')
def report(token):
    """Serve the main page — JS will fetch results using the token."""
    return render_template('index.html')


@app.route('/api/results/<token>')
def get_results(token):
    """Return stored results for a token."""
    _clean_sessions()
    session = _sessions.get(token)
    if not session:
        return jsonify({'error': 'Session not found or expired'}), 404
    return jsonify(session['results'])



if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    print(f'\n  Apt. Compliance Checker — http://localhost:{port}\n')
    app.run(debug=debug, host='0.0.0.0', port=port)
