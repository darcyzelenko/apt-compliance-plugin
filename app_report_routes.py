"""
Report generation routes — produces a Design Compliance Report .docx
that can be handed to a building certifier / RBS.

Register in app.py:
    from app_report_routes import register_report_routes
    register_report_routes(app)

Requires:
    - node / npm with 'docx' package installed globally on the server
    - generate_report.js in the same directory as app.py
"""

import json
import logging
import os
import subprocess
import tempfile
from flask import jsonify, request, send_file

log = logging.getLogger(__name__)

# Path to the node script — sits alongside app.py
_SCRIPT = os.path.join(os.path.dirname(__file__), 'generate_report.js')


def register_report_routes(app):

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_session(token):
        """Retrieve stored session data (same helper as other routes)."""
        try:
            from app import _get_session as _gs
            return _gs(token)
        except ImportError:
            pass
        # Fallback: check a simple in-memory store exposed on app
        store = getattr(app, '_session_store', {})
        return store.get(token)

    def _run_node(payload: dict) -> bytes:
        """Run generate_report.js with payload as stdin, return docx bytes."""
        if not os.path.exists(_SCRIPT):
            raise FileNotFoundError(
                'generate_report.js not found at %s — '
                'deploy it alongside app.py' % _SCRIPT
            )
        proc = subprocess.run(
            ['node', _SCRIPT],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b'').decode()
            raise RuntimeError('Report generator failed: ' + err[:400])
        if len(proc.stdout) < 1000:
            raise RuntimeError(
                'Report generator returned too little data — '
                + (proc.stderr or b'').decode()[:200]
            )
        return proc.stdout

    # ── POST /api/generate-report ────────────────────────────────────────────

    @app.route('/api/generate-report', methods=['POST'])
    def api_generate_report():
        """
        Generate a Design Compliance Report .docx.

        Accepts JSON body:
        {
            "token":        "<session token>",          // OR
            "results":      { <single-apt compliance data> },
            "building_results": { <building compliance data> },
            "jurisdiction": "VIC" | "NSW" | "BEST_PRACTICE",
            "project": {
                "name":      "...",
                "address":   "...",
                "applicant": "...",
                "designer":  "...",
                "certifier": "...",
                "ref":       "...",
                "basix":     "...",    // NSW
                "frv":       "...",    // VIC
                "storeys":   "...",
                "rise":      "...",
                "height":    "...",
                "gfa":       "..."
            }
        }
        """
        body = request.get_json(force=True, silent=True) or {}
        jurisdiction = body.get('jurisdiction', 'VIC').upper()
        project      = body.get('project', {})

        # Load results — either inline or from a stored session token
        token        = body.get('token', '')
        apt_results  = body.get('results', {})
        bld_results  = body.get('building_results', None)

        if token and not apt_results:
            session_data = _get_session(token)
            if session_data:
                # Single-apt session
                apt_results = session_data.get('results', session_data)

        # Try building token
        bld_token = body.get('building_token', '')
        if bld_token and not bld_results:
            bld_session = _get_session(bld_token)
            if bld_session:
                bld_results = bld_session.get('results', bld_session)

        if not apt_results and not bld_results:
            return jsonify({'error': 'No compliance data provided. Pass token or results directly.'}), 400

        payload = {
            'jurisdiction':     jurisdiction,
            'project':          project,
            'results':          apt_results,
            'building_results': bld_results,
            'generated_at':     __import__('datetime').datetime.utcnow().isoformat(),
        }

        try:
            docx_bytes = _run_node(payload)
        except FileNotFoundError as e:
            log.error('generate_report.js missing: %s', e)
            return jsonify({'error': str(e)}), 500
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Report generation timed out — try again'}), 500
        except RuntimeError as e:
            log.error('Report generation error: %s', e)
            return jsonify({'error': str(e)}), 500

        # Build a descriptive filename
        proj_name = project.get('name', 'Project').replace(' ', '_')[:40]
        filename  = 'Design_Compliance_Report_%s.docx' % proj_name

        # Write to temp file and send
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tf:
            tf.write(docx_bytes)
            tmp_path = tf.name

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
