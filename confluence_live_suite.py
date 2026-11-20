"""Run live retrieval and deterministic validation checks with one token prompt."""
import argparse
import hashlib
import json
import time
import uuid
from getpass import getpass
from pathlib import Path

from artifact_formats import validate_artifact
from artifact_pipeline import has_proper_heading
from connectors import make_context
from connectors.base import RETRIEVED, run_fetch
from connectors.confluence import ConfluenceConnector
from secret_store import InMemorySecretStore
from tenant_store import Connection

ROOT = Path(__file__).parent / "tests/fixtures/confluence/live-suite"


def check_case(case, result):
    if result.outcome != case["outcome"]:
        return f"expected {case['outcome']}, got {result.outcome}: {result.detail}"
    if result.outcome != RETRIEVED:
        return None
    expected = (ROOT / case["fixture"]).read_bytes()
    if (hashlib.sha256(expected).hexdigest() != case["sha256"]
            or result.data != expected):
        return "download or reference fixture differs from the recorded bytes"
    if result.filename != case["filename"] or result.version != str(case["version"]):
        return "filename or version differs from the pinned reference"
    validation = validate_artifact(result.data, case["format"])
    if validation.code != case["validation"]:
        return f"expected validation {case['validation']}, got {validation.code}"
    heading = has_proper_heading(validation, "Risk Assessment") if validation.valid else False
    if heading != case["risk_heading"]:
        return "risk assessment heading check differs from expectation"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--cloud-id", default="43c0f648-dc39-4501-a7ca-bc8bf841ad48")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    connector = ConfluenceConnector()
    settings = connector.validate_settings({"cloud_id": args.cloud_id, "email": args.email})
    conn = Connection(str(uuid.uuid4()), str(uuid.uuid4()), "confluence", settings)
    secrets = InMemorySecretStore()
    secrets.put_connection_secret(conn.org_id, conn.conn_id, getpass("Confluence API token: "))
    failed = 0
    for case in manifest["cases"]:
        ref = connector.validate_ref({"page_id": manifest["page_id"],
                                      "filename": case["filename"], "version": case["version"]})
        ctx = make_context(connector, conn, secrets, deadline=time.monotonic() + 30)
        result = run_fetch(connector, conn, ref, ctx)
        error = check_case(case, result)
        failed += bool(error)
        print(f"{'FAIL' if error else 'PASS'} {case['id']} — {error or result.outcome}")
    print(f"{len(manifest['cases']) - failed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
