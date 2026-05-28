"""
session_store.py — shared session helpers
Imported by app.py and app_building_routes.py without circular dependency.
"""
import json, os, time, threading

SESSION_TTL = 7200
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