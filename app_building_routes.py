"""
app_building_routes.py
======================
Building-level compliance routes for Apt. v0.4

Add to app.py with two lines:

    from app_building_routes import register_building_routes
    register_building_routes(app)

That's it — the function imports run_compliance and the session helpers
from the modules already present in the project.

New endpoints
─────────────
POST  /api/check-building          Run full building compliance check
GET   /api/results-building/<token>  Retrieve a stored building result
POST  /api/validate-building-dxf   Detect unit IDs without running checks
GET   /api/sample-building         Download a sample multi-apartment DXF

Form / query parameters
───────────────────────
  dxf_file    (file)    DXF with unit-prefixed layers  e.g. APT_01_ROOM_MAINBED
  jurisdiction (str)    'VIC' | 'NSW' | 'BEST_PRACTICE'  (default: 'VIC')
  ceiling_height (float) metres, default 2.7  (same as /api/check)
"""

import uuid
import time
import traceback
import logging

from flask import request, jsonify

from compliance_engine import run_compliance
from building_compliance import (
    check_building,
    building_result_to_dict,
    is_multi_apartment,
    detect_unit_ids,
)

VALID_JURISDICTIONS = {'VIC', 'NSW', 'BEST_PRACTICE'}


def register_building_routes(app):
    """Register all building-level routes on the Flask app instance."""

    # Re-use the session helpers already defined in app.py
    # (imported at call time to avoid circular imports at module level)
    from app import _get_session, _set_session

    # ── POST /api/check-building ───────────────────────────────────────────────

    @app.route('/api/check-building', methods=['POST'])
    def api_check_building():
        """
        Run compliance on a multi-apartment DXF.

        Returns the same token/URL pattern as /api/store so existing
        frontend session-retrieval code works unchanged:
          { token, url, apartments: [...], summary: {...} }
        """
        if 'dxf_file' not in request.files:
            return jsonify({'error': 'No DXF file uploaded'}), 400

        f = request.files['dxf_file']
        if not f.filename:
            return jsonify({'error': 'No file selected'}), 400

        jurisdiction = request.form.get('jurisdiction', 'VIC').strip().upper()
        if jurisdiction not in VALID_JURISDICTIONS:
            jurisdiction = 'VIC'

        try:
            ceiling_h = float(request.form.get('ceiling_height', 2.7))
            ceiling_h = max(2.1, min(5.0, ceiling_h))
        except (ValueError, TypeError):
            ceiling_h = 2.7

        try:
            dxf_text = f.read().decode('utf-8', errors='replace')
        except Exception as e:
            return jsonify({'error': f'Could not read file: {e}'}), 400

        # Pre-flight: confirm this is actually a multi-apartment file
        if not is_multi_apartment(dxf_text):
            return jsonify({
                'error': (
                    "This DXF doesn't contain multi-apartment unit prefixes. "
                    "Layers must be named like APT_01_ROOM_MAINBED, "
                    "APT_02_ROOM_LIVING, etc. "
                    "Use /api/check for single-apartment files."
                ),
                'is_multi_apartment': False,
                'unit_ids': [],
            }), 422

        try:
            result = check_building(
                dxf_text=dxf_text,
                jurisdiction=jurisdiction,
                ceiling_h=ceiling_h,
                compliance_engine_fn=run_compliance,
            )
        except ValueError as e:
            return jsonify({'error': str(e)}), 422
        except Exception as e:
            logging.error('check_building error: ' + traceback.format_exc())
            return jsonify({
                'error': f'Building compliance check failed: {e}',
                'traceback': traceback.format_exc(),
            }), 500

        result_dict = building_result_to_dict(result)

        # Store using the same session mechanism as /api/store
        token = uuid.uuid4().hex[:12]
        _set_session(token, result_dict)   # stored under results key, with ts

        base_url = request.host_url.rstrip('/')
        return jsonify({
            'token': token,
            'url': f'{base_url}/building/{token}',
            **result_dict,
        })

    # ── GET /api/results-building/<token> ─────────────────────────────────────


    @app.route('/api/debug-building-dxf', methods=['POST'])
    def api_debug_building_dxf():
        f = request.files.get('dxf_file')
        if not f:
            return jsonify({'error': 'no file'}), 400
        text = f.read().decode('utf-8', errors='replace')
        from building_compliance import detect_unit_ids
        unit_ids = detect_unit_ids(text)
        # Find all lines matching APT_XX_ pattern
        import re
        tagged = list(set(re.findall(r'APT_[A-Za-z0-9]+_(?:ROOM|STORAGE|POS|WINDOW|DOOR|NORTH|NOISE|WALL|COLUMN|OVERHANG|SHAFT|KITCHEN|BATHROOM|FURNITURE)\w*', text)))
        return jsonify({
            'unit_ids_detected': unit_ids,
            'tagged_layers_found': sorted(tagged)[:40],
            'total_lines': len(text.splitlines()),
        })


    @app.route('/api/results-building/<token>')
    def api_building_results(token):
        """
        Retrieve a previously computed building result by session token.
        Same TTL as single-apartment sessions (2 hours).
        """
        session = _get_session(token)
        if not session:
            return jsonify({'error': 'Session not found or expired'}), 404
        return jsonify({'results': session['results'], 'ts': session['ts']})

    # ── POST /api/validate-building-dxf ───────────────────────────────────────

    @app.route('/api/validate-building-dxf', methods=['POST'])
    def api_validate_building_dxf():
        """
        Fast pre-flight check: detect unit IDs without running compliance.
        Used by the frontend file picker and the Rhino plugin to confirm
        a file is correctly tagged before starting a full check.

        Returns:
          { is_multi_apartment, unit_count, unit_ids, message }
        """
        if 'dxf_file' not in request.files:
            return jsonify({'error': 'No DXF file uploaded'}), 400

        f = request.files['dxf_file']
        try:
            dxf_text = f.read().decode('utf-8', errors='replace')
        except Exception as e:
            return jsonify({'error': f'Could not read file: {e}'}), 400

        multi  = is_multi_apartment(dxf_text)
        units  = detect_unit_ids(dxf_text)

        return jsonify({
            'is_multi_apartment': multi,
            'unit_count': len(units),
            'unit_ids': units,
            'message': (
                f'Detected {len(units)} apartment{"s" if len(units) != 1 else ""}: '
                f'{", ".join(units)}'
                if units else
                'No unit prefixes detected — this appears to be a single-apartment file.'
            ),
        })

    # ── GET /building/<token> (page route) ────────────────────────────────────

    @app.route('/building/<token>')
    def building_report(token):
        """
        Serve the main page for a building report.
        JS fetches the results via /api/results-building/<token>.
        """
        # Re-use the existing index template for now; swap for building.html
        # once that template is in place.
        from flask import render_template
        return render_template('building_overview.html')

    return app