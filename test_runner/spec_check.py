"""Check a running system against spec.yaml, the contract graders test against.

    python -m test_runner.spec_check --url https://team22.13-58-35-224.sslip.io/system

Two parts, both read from spec.yaml itself, so the spec and the checks can't drift apart:
1. Every documented operation answers with a documented status, and its JSON body validates
   against the response schema (success and error responses, the testing endpoints included).
   The HTML error pages listed under "Known deviations" are not checked.
2. Every shared release in the spec's table gives the documented bundle status and category
   statuses, and its download holds the documented bytes (the sha256 values in the spec).

Prints one line per check and exits 0 if all pass, 1 otherwise. Creates bundles and one test
session on the target, like any client; it changes nothing else.
"""
import argparse
import hashlib
import io
import re
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from openapi_schema_validator import OAS30Validator, oas30_format_checker

from test_runner.client import HttpClient

SPEC_PATH = Path(__file__).resolve().parent.parent / "spec.yaml"
CATEGORIES = ("SBOM", "Risk Assessment", "Test Logs", "User Instructions")
STATUSES = ("PRESENT", "MISSING", "INVALID", "INACCESSIBLE")
TERMINAL = ("COMPLETED", "PARTIAL_SUCCESS", "FAILED")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ReleaseRow:
    """One release from the spec's shared-release table."""
    release: str
    bundle: str                      # COMPLETED, PARTIAL_SUCCESS or FAILED
    categories: dict[str, str]       # expected status per category (empty for FAILED)
    sha256: dict[str, str]           # file name in the download -> expected sha256
    sources: dict[str, str]          # category -> the external_source the table names for it


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def shared_releases(spec: dict[str, Any]) -> list[ReleaseRow]:
    """The rows of the "Shared pod releases" table in info.description."""
    text = spec["info"]["description"]
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or not cells[0].startswith("`") or cells[2] not in TERMINAL:
            continue
        test_logs_cell, bundle = cells[1], cells[2]
        expected: dict[str, str] = {}
        if bundle != "FAILED":
            expected = {category: "PRESENT" for category in CATEGORIES}
            first = test_logs_cell.split()[0].rstrip(",:")
            if first in STATUSES:
                expected["Test Logs"] = first
            for category, status in re.findall(rf"({'|'.join(CATEGORIES)}) ({'|'.join(STATUSES)})", test_logs_cell):
                expected[category] = status
        # "from Jenkins build 2", "absent at the pinned GitHub commit": Test Logs' source
        named = re.search(r"\b(Jenkins|GitHub|Confluence)\b", test_logs_cell)
        sources = {"Test Logs": named.group(1)} if named and bundle != "FAILED" else {}
        for release in re.findall(r"`([^`]+)`", cells[0]):
            rows.append(ReleaseRow(release, bundle, dict(expected), {}, dict(sources)))
    # "Build 2's file has sha256 ..." pins the Test Logs bytes of the release built from build 2
    match = re.search(r"sha256\s+`([0-9a-f]{64})`", text)
    for row in rows:
        if match and row.release == "v1.5-jenkins":
            row.sha256["test_logs.md"] = match.group(1)
    return rows


class Checker:
    def __init__(self, client, spec: dict[str, Any], poll_seconds: float = 30.0, poll_interval: float = 0.5,
                 product: str = "prd-88"):
        self.client = client
        self.spec = spec
        self.poll_seconds = poll_seconds
        self.poll_interval = poll_interval
        self.product = product
        self.results: list[Check] = []

    # Helpers

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append(Check(name, bool(ok), detail))
        return bool(ok)

    def schema_errors(self, schema_name: str, body: Any) -> str:
        schema = {"$ref": f"#/components/schemas/{schema_name}", "components": self.spec["components"]}
        errors = sorted(OAS30Validator(schema, format_checker=oas30_format_checker).iter_errors(body),
                        key=lambda e: list(e.path))
        return "; ".join(f"{'/'.join(map(str, e.path)) or '(body)'}: {e.message[:120]}" for e in errors[:3])

    def expect(self, name: str, response, status: int, schema_name: str | None) -> Any:
        """Record that response has the documented status and, for JSON, a valid body."""
        body = response.json()
        if response.status != status:
            self.record(name, False, f"HTTP {response.status}, expected {status}: {str(body)[:200]}")
            return body
        if schema_name is None:
            self.record(name, True)
            return body
        if body is None:
            self.record(name, False, "the body is not JSON")
            return body
        errors = self.schema_errors(schema_name, body)
        self.record(name, not errors, errors or "")
        return body

    def create(self, release: str) -> str | None:
        response = self.client.request("POST", "/bundles", json_body={"product_id": self.product,
                                                                      "release_version": release})
        body = response.json()
        return body.get("bundle_id") if response.status == 202 and isinstance(body, dict) else None

    def wait(self, bundle_id: str):
        deadline = time.monotonic() + self.poll_seconds
        while True:
            response = self.client.request("GET", f"/bundles/{bundle_id}")
            body = response.json()
            if response.status != 200 or not isinstance(body, dict) or body.get("status") in TERMINAL:
                return response
            if time.monotonic() >= deadline:
                return response
            time.sleep(self.poll_interval)

    # Part 1: operations

    def check_operations(self, rows: list[ReleaseRow]) -> None:
        c = self.client
        self.expect("GET / -> 200 IndexResponse", c.request("GET", "/"), 200, "IndexResponse")
        self.expect("GET /health -> 200 HealthResponse", c.request("GET", "/health"), 200, "HealthResponse")
        self.expect("GET /version -> 200 VersionResponse", c.request("GET", "/version"), 200, "VersionResponse")

        complete = next((r.release for r in rows if r.bundle == "COMPLETED"), "v1.2")
        created = c.request("POST", "/bundles", json_body={"product_id": self.product, "release_version": complete})
        body = self.expect("POST /bundles -> 202 BundleTaskResponse", created, 202, "BundleTaskResponse")
        for name, payload in (("missing release_version", {"product_id": self.product}),
                              ("blank product_id", {"product_id": " ", "release_version": complete}),
                              ("empty object", {})):
            self.expect(f"POST /bundles ({name}) -> 400 ErrorResponse",
                        c.request("POST", "/bundles", json_body=payload), 400, "ErrorResponse")

        unknown = str(uuid.uuid4())
        self.expect("GET /bundles/{unknown} -> 404 ErrorResponse",
                    c.request("GET", f"/bundles/{unknown}"), 404, "ErrorResponse")
        self.expect("GET /bundles/{unknown}/download -> 404 ErrorResponse",
                    c.request("GET", f"/bundles/{unknown}/download"), 404, "ErrorResponse")
        if isinstance(body, dict) and body.get("bundle_id"):
            done = self.wait(body["bundle_id"])
            final = self.expect("GET /bundles/{bundle_id} -> 200 BundleStatusResponse", done, 200,
                                "BundleStatusResponse")
            url = final.get("download_url") if isinstance(final, dict) else None
            if url:
                archive = c.request("GET", url)
                is_zip = archive.status == 200 and zipfile.is_zipfile(io.BytesIO(archive.body))
                self.record("GET /bundles/{bundle_id}/download -> 200 application/zip", is_zip,
                            "" if is_zip else f"HTTP {archive.status}")
            else:
                self.record("GET /bundles/{bundle_id}/download -> 200 application/zip", False, "no download_url")

        session = f"spec-check-{uuid.uuid4()}"
        setup = self.expect("POST /test/setup -> 201 TestSetupResponse",
                            c.request("POST", "/test/setup", json_body={"session_id": session, "setup": {}}),
                            201, "TestSetupResponse")
        self.expect("POST /test/setup (same session) -> 409 TestError",
                    c.request("POST", "/test/setup", json_body={"session_id": session, "setup": {}}), 409, "TestError")
        self.expect("POST /test/setup (no session_id) -> 400 TestError",
                    c.request("POST", "/test/setup", json_body={"setup": {}}), 400, "TestError")
        self.expect("GET /test/report -> 200 TestReportResponse",
                    c.request("GET", f"/test/report?session_id={session}"), 200, "TestReportResponse")
        self.expect("GET /test/report (no session_id) -> 400 TestError",
                    c.request("GET", "/test/report"), 400, "TestError")
        self.expect("GET /test/report (unknown session) -> 404 TestError",
                    c.request("GET", f"/test/report?session_id=unknown-{uuid.uuid4()}"), 404, "TestError")

        if isinstance(setup, dict) and isinstance(setup.get("fixtures"), list):
            listed = {(f.get("product_id"), f.get("release_version")) for f in setup["fixtures"] if isinstance(f, dict)}
            servable = [r.release for r in rows if r.bundle != "FAILED"]
            absent = [r for r in servable if (self.product, r) not in listed]
            self.record("POST /test/setup lists every shared release", not absent,
                        f"not listed: {', '.join(absent)}" if absent else "")

    # Part 2: the shared releases

    def check_release(self, row: ReleaseRow) -> None:
        name = f"{self.product} {row.release}"
        bundle_id = self.create(row.release)
        if bundle_id is None:
            self.record(f"{name}: POST /bundles -> 202", False)
            return
        response = self.wait(bundle_id)
        body = response.json() if response.status == 200 else None
        if not isinstance(body, dict):
            self.record(f"{name}: GET /bundles/{{bundle_id}} -> 200", False, f"HTTP {response.status}")
            return
        status = body.get("status")
        self.record(f"{name}: bundle {row.bundle}", status == row.bundle,
                    "" if status == row.bundle else f"got {status}: {body.get('error', '')}")
        errors = self.schema_errors("BundleStatusResponse", body)
        self.record(f"{name}: response matches BundleStatusResponse", not errors, errors)
        if row.bundle == "FAILED":
            return
        got = {item.get("category"): item.get("status") for item in body.get("manifest") or []}
        wrong = {c: (got.get(c), s) for c, s in row.categories.items() if got.get(c) != s}
        self.record(f"{name}: categories as documented", not wrong,
                    "; ".join(f"{c}: got {g}, expected {e}" for c, (g, e) in wrong.items()))
        by_category = {item.get("category"): item for item in body.get("manifest") or []}
        for category, source in row.sources.items():
            named = (by_category.get(category) or {}).get("external_source")
            self.record(f"{name}: {category} comes from {source}", named == source,
                        "" if named == source else f"external_source is {named!r}")
        failed = [i for i in body.get("manifest") or [] if i.get("status") in ("INVALID", "INACCESSIBLE")]
        no_detail = [i.get("category") for i in failed if not str(i.get("source_failure_detail") or "").strip()]
        if failed:
            self.record(f"{name}: failures have a source_failure_detail", not no_detail,
                        f"missing on {', '.join(no_detail)}" if no_detail else "")
        if row.sha256:
            url = body.get("download_url")
            archive = self.client.request("GET", url) if url else None
            files = {}
            if archive is not None and archive.status == 200 and zipfile.is_zipfile(io.BytesIO(archive.body)):
                with zipfile.ZipFile(io.BytesIO(archive.body)) as zipped:
                    files = {n.rsplit("/", 1)[-1]: hashlib.sha256(zipped.read(n)).hexdigest()
                             for n in zipped.namelist()}
            for filename, digest in row.sha256.items():
                self.record(f"{name}: {filename} bytes match the spec's sha256", files.get(filename) == digest,
                            "" if files.get(filename) == digest else f"got {files.get(filename) or 'no file'}")

    def run(self) -> list[Check]:
        rows = shared_releases(self.spec)
        self.record("spec.yaml lists the shared releases", bool(rows), f"{len(rows)} found")
        self.check_operations(rows)
        for row in rows:
            self.check_release(row)
        return self.results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True, help="system base URL, e.g. https://team22.13-58-35-224.sslip.io/system")
    parser.add_argument("--spec", default=str(SPEC_PATH), help="the spec to check against (default: spec.yaml)")
    parser.add_argument("--poll", type=float, default=30.0, help="seconds to wait for each bundle (default 30)")
    args = parser.parse_args(argv)

    spec = load_spec(Path(args.spec))
    results = Checker(HttpClient(args.url, timeout=15), spec, poll_seconds=args.poll).run()
    print(f"{args.url} against spec.yaml {spec['info']['version']}\n")
    for check in results:
        detail = f"  ({check.detail})" if check.detail and not check.ok else ""
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.name}{detail}")
    failed = sum(not check.ok for check in results)
    print(f"\n{len(results) - failed}/{len(results)} checks pass")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
