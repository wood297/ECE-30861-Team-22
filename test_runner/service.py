"""Tester web service: runs the acceptance suite against the system at a given URL.

    POST /test/setup   {"url": "<base url>"}   reset the target (via its /test/setup) and check it is reachable
    POST /test/run     {"url": "<base url>"}   run the acceptance suite against the target, return the report
    GET  /test/report  ?url=<base url>         latest report for that target (or the latest overall)

Optional fields for setup/run: "token" (sent as a Bearer token); for run only:
"poll_timeout" (seconds to wait for a terminal bundle status, default 5) and
"only" (list of criterion ids to run).

Run locally with: python -m test_runner.service --port 5001
"""
import argparse
import threading
import urllib.error
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from test_runner.client import HttpClient
from test_runner.suite import run_suite

app = Flask(__name__)

_lock = threading.Lock()
_setups = {}
_reports = {}
_latest = {"url": None}


def make_client(url, token=None):
    return HttpClient(url, token=token)


def _payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _target_url(data):
    url = data.get("url") or request.args.get("url")
    if not isinstance(url, str) or not url.strip():
        return None, "url is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "url must be an absolute http(s) URL"
    return url.strip().rstrip("/"), None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/test/setup", methods=["POST"])
def setup():
    data = _payload()
    url, error = _target_url(data)
    if error:
        return jsonify({"error": error}), 400

    client = make_client(url, data.get("token"))
    try:
        resp = client.request("POST", "/test/setup")
    except (urllib.error.URLError, OSError) as exc:
        return jsonify({"error": f"target unreachable: {exc}", "url": url}), 502

    if 200 <= resp.status < 300:
        target_setup = "ok"
    elif resp.status in (404, 405):
        target_setup = "not_supported"
    else:
        target_setup = f"error (HTTP {resp.status})"

    result = {"url": url, "reachable": True, "target_setup": target_setup, "target_response": resp.json()}
    with _lock:
        _setups[url] = result
    return jsonify(result), 200


@app.route("/test/run", methods=["POST"])
def run():
    data = _payload()
    url, error = _target_url(data)
    if error:
        return jsonify({"error": error}), 400

    poll_timeout = data.get("poll_timeout", 5.0)
    if isinstance(poll_timeout, bool) or not isinstance(poll_timeout, (int, float)) or not 0 <= poll_timeout <= 60:
        return jsonify({"error": "poll_timeout must be a number of seconds between 0 and 60"}), 400
    only = data.get("only")
    if only is not None and not (isinstance(only, list) and all(isinstance(i, str) for i in only)):
        return jsonify({"error": "only must be a list of criterion ids"}), 400

    report = run_suite(make_client(url, data.get("token")), target=url,
                       poll_timeout=float(poll_timeout), only=set(only) if only else None)
    with _lock:
        _reports[url] = report
        _latest["url"] = url
    return jsonify(report), 200


@app.route("/test/report", methods=["GET", "POST"])
def report():
    data = _payload()
    url = data.get("url") or request.args.get("url")
    with _lock:
        if url:
            found = _reports.get(url.strip().rstrip("/"))
        else:
            found = _reports.get(_latest["url"])
    if found is None:
        return jsonify({"error": "No report available; call POST /test/run first"}), 404
    return jsonify(found), 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the tester web service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, threaded=True)
