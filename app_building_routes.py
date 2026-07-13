"""
Report generation routes -- produces a Design Compliance Report .docx
that can be handed to a building certifier / RBS.

Register in app.py:
    from app_report_routes import register_report_routes
    register_report_routes(app)

Requires:
    - node / npm with the 'docx' package installed on the server
      (nixpacks build step: `npm install --prefix /app docx`)
    - generate_report.js in the same directory as app.py
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from flask import jsonify, request, send_file

log = logging.getLogger(__name__)

# Path to the node script -- sits alongside app.py
_SCRIPT = os.path.join(os.path.dirname(__file__), 'generate_report.js')


def _resolve_session(token):
    """
    Retrieve stored session data for a token.

    Session storage was extracted into session_store.py to break a circular
    import between app.py and the building routes. Try that first, then fall
    back to any legacy helper on the app module, then an in-memory store.
    """
    if not token:
        return None
    # 1. Canonical: session_store.py
    try:
        import session_store
        getter = getattr(session_store, 'get_session', None) or getattr(session_store, 'load', None)
        if getter:
            data = getter(token)
            if data:
                return data
    except Exception as e:
        log.debug("session_store lookup failed: %s", e)
    # 2. Legacy: a _get_session helper on the app module
    try:
        from app import _get_session as _gs  # type: ignore
        data = _gs(token)
        if data:
            return data
    except Exception as e:
        log.debug("app._get_session lookup failed: %s", e)
    return None


def register_report_routes(app):

    def _get_session(token):
        # keep the old name available; also check an app-attached store
        data = _resolve_session(token)
        if data is not None:
            return data
        store = getattr(app, '_session_store', {})
        return store.get(token)

    def _run_node(payload: dict) -> bytes:
        """Run generate_report.js with payload as stdin, return docx bytes."""
        if not os.path.exists(_SCRIPT):
            raise FileNotFoundError(
                'generate_report.js not found at %s -- deploy it alongside app.py' % _SCRIPT
            )
        node_bin = shutil.which('node')
        if not node_bin:
            raise RuntimeError(
                'node executable not found on PATH -- the report generator needs Node.js '
                '(nixpacks should provide nodejs_20).'
            )
        proc = subprocess.run(
            [node_bin, _SCRIPT],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b'').decode('utf-8', 'replace')
            raise RuntimeError('Report generator failed: ' + err[:400])
        if len(proc.stdout) < 1000:
            raise RuntimeError(
                'Report generator returned too little data -- '
                + (proc.stderr or b'').decode('utf-8', 'replace')[:200]
            )
        return proc.stdout

    @app.route('/api/generate-report', methods=['POST'])
    def api_generate_report():
        """
        Generate a Design Compliance Report .docx.

        JSON body accepts either inline compliance data or a stored session token:
        {
            "token": "...",            "building_token": "...",
            "results": {...},          "building_results": {...},
            "jurisdiction": "VIC" | "NSW" | "BEST_PRACTICE",
            "project": { "name": "...", "address": "...", ... }
        }
        """
        body = request.get_json(force=True, silent=True) or {}
        jurisdiction = body.get('jurisdiction', 'VIC').upper()
        project      = body.get('project', {})

        token        = body.get('token', '')
        apt_results  = body.get('results', {})
        bld_results  = body.get('building_results', None)

        if token and not apt_results:
            session_data = _get_session(token)
            if session_data:
                apt_results = session_data.get('results', session_data)

        bld_token = body.get('building_token', '')
        if bld_token and not bld_results:
            bld_session = _get_session(bld_token)
            if bld_session:
                bld_results = bld_session.get('results', bld_session)

        if not apt_results and not bld_results:
            return jsonify({'error': 'No compliance data provided. Pass token or results directly.'}), 400

        import datetime
        payload = {
            'jurisdiction':     jurisdiction,
            'project':          project,
            'results':          apt_results,
            'building_results': bld_results,
            'generated_at':     datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        try:
            docx_bytes = _run_node(payload)
        except FileNotFoundError as e:
            log.error('generate_report.js missing: %s', e)
            return jsonify({'error': str(e)}), 500
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Report generation timed out -- try again'}), 500
        except RuntimeError as e:
            log.error('Report generation error: %s', e)
            return jsonify({'error': str(e)}), 500

        proj_name = (project.get('name', 'Project') or 'Project').replace(' ', '_')[:40]
        filename  = 'Design_Compliance_Report_%s.docx' % proj_name

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tf:
                tf.write(docx_bytes)
                tmp_path = tf.name
            return send_file(
                tmp_path,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            )
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            log.error('Failed to send report: %s', e)
            return jsonify({'error': 'Failed to send report: ' + str(e)}), 500