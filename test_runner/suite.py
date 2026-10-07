"""Cross-team acceptance suite for the pod's common web-service interface (spec 1.0.0).

Every check goes through the public Section 5 interface only (POST /bundles and
GET /bundles/{bundle_id}), so the suite can run against any team's base URL.
Expected results come from oracle.json, which the pod fixes before running an
implementation (Phase 2 section 4.2).

Each criterion records a list of checks. A check is True (pass), False (fail) or
None (not observable through this team's interface, e.g. no artifact content
exposed). A criterion fails if any check fails, passes if at least one passes and
none fail, and is skipped otherwise.
"""
import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent
DEFAULT_ORACLE = PACKAGE_DIR / "oracle.json"
DEFAULT_FIXTURES = PACKAGE_DIR.parent / "fixtures"

TERMINAL_STATUSES = {"COMPLETED", "PARTIAL_SUCCESS", "FAILED"}
BUNDLE_STATUSES = {"PENDING", "PROCESSING"} | TERMINAL_STATUSES
CATEGORY_STATUSES = {"PRESENT", "MISSING", "INVALID", "INACCESSIBLE"}


class Abort(Exception):
    """A prerequisite check failed, so the rest of the criterion cannot run."""


class Skip(Exception):
    pass


class Case:
    def __init__(self):
        self.checks = []

    def check(self, description, passed, detail=None):
        self.checks.append({"description": description, "passed": passed, "detail": detail})
        return passed

    def require(self, description, passed, detail=None):
        if not self.check(description, passed, detail):
            raise Abort(description)


class Criterion:
    def __init__(self, criterion_id, title, requirements, fn):
        self.id = criterion_id
        self.title = title
        self.requirements = list(requirements)
        self.fn = fn


CRITERIA = []


def criterion(criterion_id, title, requirements=()):
    def register(fn):
        CRITERIA.append(Criterion(criterion_id, title, requirements, fn))
        return fn
    return register


class BundleRun:
    def __init__(self):
        self.post_status = None
        self.post_body = None
        self.bundle_id = None
        self.get_status = None
        self.body = None
        self.reached_terminal = False
        self.contents = None


class Context:
    def __init__(self, client, oracle, fixtures_root, poll_timeout, poll_interval):
        self.client = client
        self.oracle = oracle
        self.fixtures_root = Path(fixtures_root)
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self._runs = {}

    def fixture(self, key):
        return self.oracle["releases"][key]

    def create_bundle(self, product_id, release_version):
        """POST a bundle request, then poll GET until a terminal status or the timeout."""
        run = BundleRun()
        resp = self.client.request("POST", "/bundles", json_body={
            "product_id": product_id,
            "release_version": release_version,
        })
        run.post_status = resp.status
        run.post_body = resp.json()
        if resp.status != 202 or not isinstance(run.post_body, dict) or not run.post_body.get("bundle_id"):
            return run
        run.bundle_id = str(run.post_body["bundle_id"])

        deadline = time.monotonic() + self.poll_timeout
        while True:
            resp = self.client.request("GET", f"/bundles/{run.bundle_id}")
            run.get_status = resp.status
            run.body = resp.json()
            if resp.status != 200 or not isinstance(run.body, dict):
                break
            if run.body.get("status") in TERMINAL_STATUSES:
                run.reached_terminal = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval)
        return run

    def bundle(self, case, key, fresh=False):
        """Bundle for a fixture release. Reused across criteria unless fresh=True."""
        fx = self.fixture(key)
        if fresh or key not in self._runs:
            run = self.create_bundle(fx["product_id"], fx["release_version"])
            if key not in self._runs:
                self._runs[key] = run
        else:
            run = self._runs[key]

        label = f"{fx['product_id']} {fx['release_version']}"
        case.require(f"POST /bundles for {label} returns 202 with a bundle_id",
                     run.bundle_id is not None,
                     f"HTTP {run.post_status}: {_short(run.post_body)}")
        case.require(f"GET /bundles/{{bundle_id}} for {label} returns 200 with a JSON object",
                     run.get_status == 200 and isinstance(run.body, dict),
                     f"HTTP {run.get_status}: {_short(run.body)}")
        return run

    def expected_contents(self, key):
        """{category: bytes} of the fixture files the oracle expects to be PRESENT."""
        fx = self.fixture(key)
        folder = self.fixtures_root / fx["product_id"] / fx["release_version"]
        return {
            category: (folder / self.oracle["files"][category]).read_bytes()
            for category, status in fx["categories"].items()
            if status == "PRESENT"
        }

    def collected_contents(self, run):
        """[(category or None, bytes)] the bundle exposes, or None if it exposes no content.

        Content is read from artifacts[].content in the status response and from the
        download_url (a zip archive, or JSON with an artifacts list) when present.
        """
        if run.contents is not None:
            return run.contents or None

        items = []
        observable = False
        artifacts = run.body.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, dict) and isinstance(artifact.get("content"), str):
                    observable = True
                    items.append((artifact.get("category"), artifact["content"].encode("utf-8")))

        url = run.body.get("download_url")
        if isinstance(url, str) and url:
            resp = self.client.request("GET", url)
            if resp.status == 200 and zipfile.is_zipfile(io.BytesIO(resp.body)):
                observable = True
                with zipfile.ZipFile(io.BytesIO(resp.body)) as archive:
                    for name in archive.namelist():
                        if not name.endswith("/"):
                            items.append((None, archive.read(name)))
            elif resp.status == 200 and isinstance(resp.json(), dict):
                for artifact in resp.json().get("artifacts") or []:
                    if isinstance(artifact, dict) and isinstance(artifact.get("content"), str):
                        observable = True
                        items.append((artifact.get("category"), artifact["content"].encode("utf-8")))

        run.contents = items if observable else []
        return items if observable else None


def _short(value, limit=200):
    text = value if isinstance(value, str) else json.dumps(value) if value is not None else "no body"
    return text if len(text) <= limit else text[:limit] + "..."


def _norm(category):
    # Category names are compared case- and whitespace-insensitively across teams
    return " ".join(str(category).split()).casefold()


def _manifest_index(body):
    index = {}
    for item in body.get("manifest") or []:
        if isinstance(item, dict) and "category" in item:
            index.setdefault(_norm(item["category"]), []).append(item)
    return index


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def check_categories(case, run, expected):
    index = _manifest_index(run.body)
    for category, status in expected.items():
        actual = [item.get("status") for item in index.get(_norm(category), [])]
        case.check(f"manifest reports '{category}' exactly once as {status}",
                   actual == [status], f"got {actual or 'no entry'}")


def check_not_collected(case, ctx, run, category):
    """REQ-06: an unsatisfied category must not appear as a collected artifact."""
    contents = ctx.collected_contents(run)
    labelled = [c for c, _ in contents or [] if c is not None]
    if not labelled:
        case.check(f"no collected artifact is labelled '{category}'", None,
                   "bundle does not expose per-category artifact content")
        return
    hits = [c for c in labelled if _norm(c) == _norm(category)]
    case.check(f"no collected artifact is labelled '{category}'", not hits,
               f"{len(hits)} artifact(s) labelled '{category}'")


# --- Interface (Section 5) --------------------------------------------------

@criterion("API-01", "POST /bundles acknowledges a valid request with 202, a UUID bundle_id and PENDING",
           ["REQ-01"])
def api_create(ctx, case):
    run = ctx.bundle(case, "complete")
    try:
        uuid.UUID(run.bundle_id)
        is_uuid = True
    except ValueError:
        is_uuid = False
    case.check("bundle_id is a UUID", is_uuid, run.bundle_id)
    case.check("initial status is PENDING", run.post_body.get("status") == "PENDING",
               f"got {run.post_body.get('status')!r}")


@criterion("API-02", "POST /bundles rejects requests missing a required field with 400", ["REQ-01"])
def api_required_fields(ctx, case):
    payloads = [
        ("missing product_id", {"release_version": "v1.2"}),
        ("missing release_version", {"product_id": "prd-88"}),
        ("empty JSON object", {}),
    ]
    for label, payload in payloads:
        resp = ctx.client.request("POST", "/bundles", json_body=payload)
        body = resp.json()
        case.check(f"{label}: 400 with an 'error' message",
                   resp.status == 400 and isinstance(body, dict) and "error" in body,
                   f"HTTP {resp.status}: {_short(body if body is not None else resp.body.decode('utf-8', 'replace'))}")


@criterion("API-03", "POST /bundles enforces field constraints (non-empty strings, JSON body)", ["REQ-01"])
def api_field_constraints(ctx, case):
    payloads = [
        ("empty product_id", {"product_id": "", "release_version": "v1.2"}),
        ("blank release_version", {"product_id": "prd-88", "release_version": "   "}),
        ("non-string product_id", {"product_id": 88, "release_version": "v1.2"}),
    ]
    for label, payload in payloads:
        resp = ctx.client.request("POST", "/bundles", json_body=payload)
        case.check(f"{label}: 400", resp.status == 400, f"HTTP {resp.status}")
    resp = ctx.client.request("POST", "/bundles", raw_body=b"not json",
                              headers={"Content-Type": "text/plain"})
    case.check("non-JSON body: 4xx client error", 400 <= resp.status < 500, f"HTTP {resp.status}")


@criterion("API-04", "GET /bundles/{bundle_id} returns 404 for an unknown bundle", ["REQ-04"])
def api_unknown_bundle(ctx, case):
    resp = ctx.client.request("GET", f"/bundles/{uuid.uuid4()}")
    case.check("unknown UUID: 404", resp.status == 404, f"HTTP {resp.status}")


@criterion("API-05", "GET /bundles/{bundle_id} conforms to BundleStatusResponse",
           ["REQ-03", "REQ-04", "REQ-05"])
def api_status_schema(ctx, case):
    run = ctx.bundle(case, "complete")
    fx = ctx.fixture("complete")
    body = run.body
    case.check("bundle_id matches the created bundle", body.get("bundle_id") == run.bundle_id,
               f"got {body.get('bundle_id')!r}")
    case.check("product_id echoes the request", body.get("product_id") == fx["product_id"],
               f"got {body.get('product_id')!r}")
    case.check("release_version echoes the request", body.get("release_version") == fx["release_version"],
               f"got {body.get('release_version')!r}")
    case.check("status is in the BundleStatusResponse enum", body.get("status") in BUNDLE_STATUSES,
               f"got {body.get('status')!r}")
    manifest = body.get("manifest")
    case.require("manifest is a list", isinstance(manifest, list), f"got {type(manifest).__name__}")
    bad = [item for item in manifest
           if not (isinstance(item, dict) and isinstance(item.get("category"), str)
                   and item.get("status") in CATEGORY_STATUSES)]
    case.check("every manifest item has a category and a status in the ArtifactManifestItem enum",
               not bad, f"invalid items: {_short(bad)}" if bad else None)
    if "download_url" in body:
        case.check("download_url is a string", isinstance(body["download_url"], str),
                   f"got {body['download_url']!r}")


@criterion("API-06", "A bundle reaches a terminal status (COMPLETED, PARTIAL_SUCCESS, FAILED)", ["REQ-05"])
def api_terminal_status(ctx, case):
    run = ctx.bundle(case, "complete")
    case.check(f"terminal status reached within {ctx.poll_timeout}s", run.reached_terminal,
               f"last status {run.body.get('status')!r}")


@criterion("API-07", "An unknown release is not reported as collected", ["REQ-01", "REQ-06"])
def api_unknown_release(ctx, case):
    fx = ctx.fixture("unknown")
    run = ctx.create_bundle(fx["product_id"], fx["release_version"])
    if run.bundle_id is None:
        # Rejecting the request outright is an acceptable outcome (open question in spec 3.3)
        case.check("request rejected with a 4xx client error", 400 <= (run.post_status or 0) < 500,
                   f"HTTP {run.post_status}")
        return
    case.require("GET /bundles/{bundle_id} returns 200", run.get_status == 200, f"HTTP {run.get_status}")
    case.check("bundle is not COMPLETED", run.body.get("status") != "COMPLETED",
               f"got {run.body.get('status')!r}")
    present = [item for item in run.body.get("manifest") or [] if item.get("status") == "PRESENT"]
    case.check("no category is PRESENT", not present, _short(present) if present else None)
    contents = ctx.collected_contents(run)
    case.check("no artifact content is included", not contents,
               f"{len(contents)} artifact(s) returned" if contents else None)


# --- Acceptance criteria (Phase 2 section 4.1) ------------------------------

@criterion("AC-01", "Release identification: artifacts belong to the requested release",
           ["REQ-01", "REQ-04"])
def ac01_release_identification(ctx, case):
    for key in ("complete", "missing"):
        fx = ctx.fixture(key)
        run = ctx.bundle(case, key)
        label = f"{fx['product_id']} {fx['release_version']}"
        case.check(f"{label}: bundle records the requested product_id and release_version",
                   (run.body.get("product_id"), run.body.get("release_version"))
                   == (fx["product_id"], fx["release_version"]),
                   f"got ({run.body.get('product_id')!r}, {run.body.get('release_version')!r})")

        artifacts = [a for a in run.body.get("artifacts") or [] if isinstance(a, dict)]
        tagged = [a for a in artifacts if "release_version" in a or "product_id" in a]
        if tagged:
            wrong = [a for a in tagged if a.get("product_id") != fx["product_id"]
                     or a.get("release_version") != fx["release_version"]]
            case.check(f"{label}: every artifact is associated with the requested release", not wrong,
                       f"{len(wrong)} artifact(s) tagged with another release" if wrong else None)

    # Content from one release must not leak into the other's bundle
    complete_hashes = {_sha256(b) for b in ctx.expected_contents("complete").values()}
    missing_hashes = {_sha256(b) for b in ctx.expected_contents("missing").values()}
    for key, foreign in (("complete", missing_hashes - complete_hashes),
                         ("missing", complete_hashes - missing_hashes)):
        fx = ctx.fixture(key)
        contents = ctx.collected_contents(ctx.bundle(case, key))
        description = f"{fx['product_id']} {fx['release_version']}: no artifact from another release is included"
        if contents is None:
            case.check(description, None, "bundle does not expose artifact content")
            continue
        leaked = [c for c, data in contents if _sha256(data) in foreign]
        case.check(description, not leaked, f"foreign content found: {leaked}" if leaked else None)


@criterion("AC-02", "Complete artifact collection: every category PRESENT and bundle COMPLETED",
           ["REQ-02", "REQ-03", "REQ-05"])
def ac02_complete_collection(ctx, case):
    fx = ctx.fixture("complete")
    run = ctx.bundle(case, "complete")
    check_categories(case, run, fx["categories"])
    case.check(f"bundle status is {fx['expected_bundle_status']}",
               run.body.get("status") == fx["expected_bundle_status"],
               f"got {run.body.get('status')!r}")


@criterion("AC-03", "Missing required artifact is reported as MISSING", ["REQ-03", "REQ-06"])
def ac03_missing_artifact(ctx, case):
    fx = ctx.fixture("missing")
    run = ctx.bundle(case, "missing")
    check_categories(case, run, fx["categories"])
    case.check("bundle is not reported as COMPLETED", run.body.get("status") != "COMPLETED",
               f"got {run.body.get('status')!r}")
    for category, status in fx["categories"].items():
        if status != "PRESENT":
            check_not_collected(case, ctx, run, category)


@criterion("AC-04", "Invalid artifact is reported as INVALID and not treated as collected",
           ["REQ-03", "REQ-06"])
def ac04_invalid_artifact(ctx, case):
    fx = ctx.fixture("invalid")
    run = ctx.bundle(case, "invalid")
    check_categories(case, run, fx["categories"])
    case.check("bundle is not reported as COMPLETED", run.body.get("status") != "COMPLETED",
               f"got {run.body.get('status')!r}")
    for category, status in fx["categories"].items():
        if status != "PRESENT":
            check_not_collected(case, ctx, run, category)


@criterion("AC-05", "External source failure is reported as INACCESSIBLE", ["REQ-02", "REQ-06", "REQ-07"])
def ac05_source_failure(ctx, case):
    raise Skip("Phase 3 uses a single controlled source; a source failure cannot be induced through "
               "the shared interface. Deferred to Phase 4 (Phase 2 section 6.1).")


@criterion("AC-06", "Source fidelity: collected content is byte-for-byte identical to the source",
           ["REQ-05", "REQ-08"])
def ac06_source_fidelity(ctx, case):
    run = ctx.bundle(case, "complete")
    contents = ctx.collected_contents(run)
    case.require("bundle exposes artifact content (artifacts[].content or download_url)",
                 contents is not None, "neither artifacts[].content nor a usable download_url found")
    collected = {_sha256(data) for _, data in contents}
    for category, data in ctx.expected_contents("complete").items():
        case.check(f"'{category}' content is preserved exactly (sha256)", _sha256(data) in collected,
                   f"expected {_sha256(data)[:16]}..., not found among {len(collected)} artifact(s)")


@criterion("AC-07", "Repeatable generation: identical inputs give equivalent manifests",
           ["REQ-04", "REQ-05", "REQ-08"])
def ac07_repeatable(ctx, case):
    first = ctx.bundle(case, "complete")
    second = ctx.bundle(case, "complete", fresh=True)
    case.check("each request creates a distinct bundle", first.bundle_id != second.bundle_id,
               f"both returned {first.bundle_id}")

    def fingerprint(run):
        body = run.body
        manifest = sorted((_norm(i.get("category")), i.get("status"))
                          for i in body.get("manifest") or [] if isinstance(i, dict))
        contents = ctx.collected_contents(run)
        hashes = sorted(_sha256(data) for _, data in contents) if contents is not None else None
        return {
            "product_id": body.get("product_id"),
            "release_version": body.get("release_version"),
            "status": body.get("status"),
            "manifest": manifest,
            "content_hashes": hashes,
        }

    a, b = fingerprint(first), fingerprint(second)
    for field in a:
        case.check(f"{field} is identical across both runs", a[field] == b[field],
                   None if a[field] == b[field] else f"{_short(a[field])} != {_short(b[field])}")


@criterion("AC-08", "Annex VII coverage: no supported category disappears silently (automated part)",
           ["REQ-03", "REQ-05"])
def ac08_coverage(ctx, case):
    categories = list(ctx.oracle["files"])
    for key in ("complete", "missing", "invalid"):
        fx = ctx.fixture(key)
        run = ctx.bundle(case, key)
        index = _manifest_index(run.body)
        for category in categories:
            items = index.get(_norm(category), [])
            statuses = [i.get("status") for i in items]
            case.check(f"{fx['release_version']}: '{category}' appears exactly once with a valid status",
                       len(items) == 1 and statuses[0] in CATEGORY_STATUSES,
                       f"got {statuses or 'no entry'}")


def run_suite(client, target=None, oracle_path=DEFAULT_ORACLE, fixtures_root=DEFAULT_FIXTURES,
              poll_timeout=5.0, poll_interval=0.25, only=None):
    """Run every criterion (or those whose id is in `only`) and return a JSON-serialisable report."""
    oracle = json.loads(Path(oracle_path).read_text(encoding="utf-8"))
    ctx = Context(client, oracle, fixtures_root, poll_timeout, poll_interval)
    started = datetime.now(timezone.utc)
    t_start = time.monotonic()
    results = []

    for crit in CRITERIA:
        if only and crit.id not in only:
            continue
        case = Case()
        outcome = None
        reason = None
        t0 = time.monotonic()
        try:
            crit.fn(ctx, case)
        except Skip as exc:
            outcome, reason = "skipped", str(exc)
        except Abort:
            pass
        except Exception as exc:  # target unreachable, malformed response, etc.
            outcome, reason = "error", f"{type(exc).__name__}: {exc}"

        if outcome is None:
            verdicts = [c["passed"] for c in case.checks]
            if any(v is False for v in verdicts):
                outcome = "failed"
            elif any(v is True for v in verdicts):
                outcome = "passed"
            else:
                outcome, reason = "skipped", "no check was observable through the interface"

        results.append({
            "id": crit.id,
            "title": crit.title,
            "requirements": crit.requirements,
            "outcome": outcome,
            "reason": reason,
            "checks": case.checks,
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        })

    summary = {key: sum(r["outcome"] == key for r in results)
               for key in ("passed", "failed", "skipped", "error")}
    summary["total"] = len(results)
    return {
        "run_id": str(uuid.uuid4()),
        "kind": "acceptance",
        "target": target,
        "spec_version": oracle.get("spec_version"),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.monotonic() - t_start) * 1000, 1),
        "passed": summary["failed"] == 0 and summary["error"] == 0,
        "summary": summary,
        "results": results,
    }
