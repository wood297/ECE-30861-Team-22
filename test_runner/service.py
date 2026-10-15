"""Tester web service, following the course's Cross-team Testing Interface.

    POST /test/setup   {"target_url": "<system base url>"}
        Creates a session ID, calls the system's POST /test/setup with it and waits
        until the system is ready. 201 {"session_id": ...}. A system setup error is
        returned with the system's own status, message and source.
    POST /test/run     {"session_id": "..."}
        Runs the acceptance suite once against the session's target. 200 with the
        suite report; 500 {"message", "source": "tester"} if the tester aborts.
        409 if the session already ran or is running.
    GET  /test/report  ?session_id=...
        Returns the stored report without running anything; 409 before the suite ends.

Optional fields: "token" on setup (sent to the target as a Bearer token); "poll_timeout"
(seconds to wait for a terminal bundle status, default 5) and "only" (list of test ids)
on run. Errors are {"message": ..., "source": "tester" | "system" | "communication" | "unknown"}.

Sessions and reports live in memory until the process stops, so run a single process
(one gunicorn worker) in production.

Run locally with: python -m test_runner.service --port 5001
"""
import argparse
import threading
import uuid
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from test_runner.client import HttpClient
from test_runner.session import SetupError, setup_target
from test_runner.suite import aborted_report, run_suite

app = Flask(__name__)

_lock = threading.Lock()
# session_id -> {"target_url", "token", "state": ready|running|done|aborted, "report"}
_sessions = {}


def make_client(url, token=None):
    return HttpClient(url, token=token)


def error(message, status, source="tester"):
    return jsonify({"message": message, "source": source}), status


def _payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _target_url(data):
    url = data.get("target_url")
    if not isinstance(url, str) or not url.strip():
        return None, "target_url is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "target_url must be an absolute http(s) URL"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None, "target_url must be a base URL without credentials, query or fragment"
    return url.strip().rstrip("/"), None


def _session_id(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/test/setup", methods=["POST"])
def setup():
    data = _payload()
    if data is None:
        return error("Request body must be a JSON object with target_url", 400)
    url, problem = _target_url(data)
    if problem:
        return error(problem, 400)
    token = data.get("token")
    if token is not None and not isinstance(token, str):
        return error("token must be a string", 400)

    session_id = str(uuid.uuid4())
    try:
        setup_target(make_client(url, token), session_id)
    except SetupError as exc:
        return jsonify(exc.body()), exc.status
    except Exception as exc:
        return error(f"Tester setup failed: {type(exc).__name__}: {exc}", 500)

    with _lock:
        _sessions[session_id] = {"target_url": url, "token": token, "state": "ready", "report": None}
    return jsonify({"session_id": session_id}), 201


@app.route("/test/run", methods=["POST"])
def run():
    data = _payload()
    if data is None:
        return error("Request body must be a JSON object with session_id", 400)
    session_id = _session_id(data.get("session_id"))
    if session_id is None:
        return error("session_id is required", 400)

    # Invalid requests are rejected before they can consume the session
    poll_timeout = data.get("poll_timeout", 5.0)
    if isinstance(poll_timeout, bool) or not isinstance(poll_timeout, (int, float)) or not 0 <= poll_timeout <= 60:
        return error("poll_timeout must be a number of seconds between 0 and 60", 400)
    only = data.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(i, str) for i in only)):
        return error("only must be a list of test ids", 400)

    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return error(f"Unknown session {session_id}; call POST /test/setup first", 404)
        if session["state"] != "ready":
            return error(f"Session {session_id} has already run (state: {session['state']}); "
                         "create a new session with POST /test/setup to repeat the suite", 409)
        session["state"] = "running"

    url = session["target_url"]
    try:
        report = run_suite(make_client(url, session["token"]), session_id=session_id, target=url,
                           poll_timeout=float(poll_timeout), only=set(only) if only else None)
    except Exception as exc:
        report = aborted_report(session_id, url, f"{type(exc).__name__}: {exc}", set(only) if only else None)

    with _lock:
        session["report"] = report
        session["state"] = "aborted" if report.get("aborted") else "done"
    if report.get("aborted"):
        return error(f"The tester aborted: {report['abort_message']}. "
                     "Retrieve the available results with GET /test/report.", 500)
    return jsonify(report), 200


@app.route("/test/report", methods=["GET"])
def report():
    session_id = _session_id(request.args.get("session_id"))
    if session_id is None:
        return error("session_id query parameter is required", 400)
    with _lock:
        session = _sessions.get(session_id)
        if session is None:
            return error(f"Unknown session {session_id}", 404)
        if session["report"] is None:
            return error(f"The suite for session {session_id} has not ended (state: {session['state']}); "
                         "try again later", 409)
        return jsonify(session["report"]), 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the tester web service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
