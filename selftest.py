"""Testing endpoints for the validated system (this service).

    POST /test/setup   clear stored bundles and list the fixture releases available
    POST /test/run     run this repository's pytest suite and return the report
    GET  /test/report  return the report from the last /test/run
"""
import os
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify

from artifact_pipeline import FIXTURES_ROOT

REPO_ROOT = Path(__file__).parent
# Set while a self-test is running so a test that calls /test/run cannot recurse
SELFTEST_ENV = "CRA_SELFTEST_RUNNING"

_last_report = {"report": None}


def list_fixtures():
    if not FIXTURES_ROOT.is_dir():
        return []
    return [
        {"product_id": product.name, "release_version": release.name}
        for product in sorted(FIXTURES_ROOT.iterdir()) if product.is_dir()
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


def create_test_blueprint(reset_state):
    bp = Blueprint("selftest", __name__, url_prefix="/test")

    @bp.route("/setup", methods=["POST"])
    def setup():
        reset_state()
        return jsonify({"status": "ready", "fixtures": list_fixtures()}), 200

    @bp.route("/run", methods=["POST"])
    def run():
        if os.environ.get(SELFTEST_ENV):
            return jsonify({"error": "A self-test is already running in this process"}), 409
        report = run_internal_tests()
        _last_report["report"] = report
        return jsonify(report), 200

    @bp.route("/report", methods=["GET"])
    def report():
        if _last_report["report"] is None:
            return jsonify({"error": "No report available; call POST /test/run first"}), 404
        return jsonify(_last_report["report"]), 200

    return bp
