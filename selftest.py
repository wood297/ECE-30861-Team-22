"""Testing endpoints for the validated system (this service).

Required by the Cross-team Testing Interface:

    POST /test/setup   {"session_id": "...", "setup": {}}
        Registers the session and checks the fixture source is available.
        201 {"session_id": ...}; 409 if the ID already exists; 500 if preparation fails.
    GET  /test/report  ?session_id=...
        Code coverage measured for the session: {"session_id", "coverage": {"code": {...}}}.

Extra (not part of the course interface):

    POST /test/selftest   run this repository's pytest suite and return the report
    GET  /test/selftest   return the report from the last self-test

Bundles are isolated by their own UUID, so setup needs no reset and sessions cannot
interfere with each other. Errors are {"message": ..., "source": "system"}.
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

import artifact_pipeline

REPO_ROOT = Path(__file__).parent
# Set while a self-test is running so a test that calls /test/selftest cannot recurse
SELFTEST_ENV = "CRA_SELFTEST_RUNNING"
CODE_COVERAGE_BASIS = ("Source lines; per-session code coverage instrumentation is not implemented "
                       "yet, so counts are unavailable")

_lock = threading.Lock()
_sessions = {}
_last_report = {"report": None}


def error(message, status):
    return jsonify({"message": message, "source": "system"}), status


def list_fixtures():
    root = artifact_pipeline.FIXTURES_ROOT
    if not root.is_dir():
        return []
    return [
        {"product_id": product.name, "release_version": release.name}
        for product in sorted(root.iterdir()) if product.is_dir()
        for release in sorted(product.iterdir()) if release.is_dir()
    ]


def parse_junit(path):
    results = []
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome, reason = "passed", None
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed" if child.tag == "failure" else "error"
                reason = child.get("message")
            elif child.tag == "skipped":
                outcome = "xfailed" if child.get("type") == "pytest.xfail" else "skipped"
                reason = child.get("message")
        results.append({
            "id": f"{case.get('classname')}::{case.get('name')}",
            "outcome": outcome,
            "reason": reason,
            "duration_ms": round(float(case.get("time") or 0) * 1000, 1),
        })
    return results


def run_internal_tests(test_path="tests", timeout=300):
    """Run pytest in a subprocess so tests cannot touch this process's live state."""
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q", "-p", "no:cacheprovider",
             f"--junitxml={junit}"],
            cwd=REPO_ROOT, env=dict(os.environ, **{SELFTEST_ENV: "1"}),
            capture_output=True, text=True, timeout=timeout,
        )
        results = parse_junit(junit) if junit.exists() else []

    summary = {key: sum(r["outcome"] == key for r in results)
               for key in ("passed", "failed", "skipped", "xfailed", "error")}
    summary["total"] = len(results)
    return {
        "run_id": str(uuid.uuid4()),
        "kind": "self-test",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "summary": summary,
        "results": results,
        "output_tail": proc.stdout[-4000:],
    }


def create_test_blueprint():
    bp = Blueprint("selftest", __name__, url_prefix="/test")

    @bp.route("/setup", methods=["POST"])
    def setup():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error("Request body must be a JSON object with session_id", 400)
        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return error("session_id must be a non-empty string", 400)
        setup_data = data.get("setup", {})
        if not isinstance(setup_data, dict):
            return error("setup must be a JSON object (use {} when there is no starting data)", 400)

        # Phase 3 reads every release from one controlled source: the fixtures folder
        fixtures = list_fixtures()
        if not fixtures:
            return error("Could not prepare the session: the fixture source is missing or empty", 500)

        with _lock:
            if session_id in _sessions:
                return error(f"Session {session_id} already exists", 409)
            _sessions[session_id] = {"created_at": datetime.now(timezone.utc).isoformat(), "setup": setup_data}
        return jsonify({"session_id": session_id, "fixtures": fixtures}), 201

    @bp.route("/report", methods=["GET"])
    def report():
        session_id = request.args.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return error("session_id query parameter is required", 400)
        with _lock:
            if session_id not in _sessions:
                return error(f"Unknown session {session_id}", 404)
        return jsonify({
            "session_id": session_id,
            "coverage": {"code": {"covered": None, "total": None, "basis": CODE_COVERAGE_BASIS}},
        }), 200

    @bp.route("/selftest", methods=["POST"])
    def run_selftest():
        if os.environ.get(SELFTEST_ENV):
            return error("A self-test is already running in this process", 409)
        report = run_internal_tests()
        _last_report["report"] = report
        return jsonify(report), 200

    @bp.route("/selftest", methods=["GET"])
    def selftest_report():
        if _last_report["report"] is None:
            return error("No self-test report yet; call POST /test/selftest first", 404)
        return jsonify(_last_report["report"]), 200

    return bp
