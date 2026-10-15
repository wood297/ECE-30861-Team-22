"""Cross-team acceptance suite for the pod's common web-service interface (spec 1.0.0).

Every check goes through the public Section 5 interface only (POST /bundles and
GET /bundles/{bundle_id}), so the suite can run against any team's base URL.
Expected results come from oracle.json, which the pod fixes before running an
implementation (Phase 2 section 4.2).

Each criterion records a list of checks. A check is True (pass), False (fail) or
None (not observable through this team's interface, e.g. no artifact content
exposed). A criterion fails if any check fails, passes if at least one passes and
none fail, and is skipped otherwise.

The report follows the Cross-team Testing Interface: one result per planned test
with outcome pass / fail / error / skip, a source for every error, and code,
component and system coverage.
"""
import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from test_runner.session import NETWORK_ERRORS

PACKAGE_DIR = Path(__file__).parent
DEFAULT_ORACLE = PACKAGE_DIR / "oracle.json"
DEFAULT_FIXTURES = PACKAGE_DIR.parent / "fixtures"

TERMINAL_STATUSES = {"COMPLETED", "PARTIAL_SUCCESS", "FAILED"}
BUNDLE_STATUSES = {"PENDING", "PROCESSING"} | TERMINAL_STATUSES
CATEGORY_STATUSES = {"PRESENT", "MISSING", "INVALID", "INACCESSIBLE"}
OUTCOMES = ("pass", "fail", "error", "skip")

# Upper bound on all target calls in one run, so the registered run limit holds
# even when every target request hangs until its timeout
RUN_BUDGET = 120.0
COVERAGE_TIMEOUT = 5


class Abort(Exception):
    """A prerequisite check failed, so the rest of the criterion cannot run."""


class Skip(Exception):
    pass


class CommunicationError(Exception):
    """No response from the target (refused, timed out, dropped) or the run budget ran out."""


class TargetError(Exception):
    """The target answered with a 5xx: an internal failure reported by the system."""


class TesterAbort(Exception):
    """The tester itself cannot continue (e.g. its fixtures are missing); stops the suite."""


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
    def __init__(self, criterion_id, title, requirements, components, fn):
        self.id = criterion_id
        self.title = title
        self.requirements = list(requirements)
        self.components = list(components)
        self.fn = fn


CRITERIA = []


def criterion(criterion_id, title, requirements=(), components=()):
    def register(fn):
        CRITERIA.append(Criterion(criterion_id, title, requirements, components, fn))
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
    def __init__(self, client, oracle, fixtures_root, poll_timeout, poll_interval,
                 session_id=None, time_budget=RUN_BUDGET):
        self.client = client
        self.oracle = oracle
        self.fixtures_root = Path(fixtures_root)
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.session_id = session_id
        self.time_budget = time_budget
        self.deadline = time.monotonic() + time_budget
        self._runs = {}

    def request(self, method, path, **kwargs):
        """Send a project API request; no response -> CommunicationError, 5xx -> TargetError."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CommunicationError(f"Not evaluated: the tester's {self.time_budget:g}s budget for target "
                                     f"calls ran out because earlier requests were slow or timed out.")
        try:
            resp = self.client.request(method, path, timeout=remaining, **kwargs)
        except NETWORK_ERRORS as exc:
            raise CommunicationError(f"{method} {path} got no response: {exc}")
        if resp.status >= 500:
            body = resp.json()
            raise TargetError(f"{method} {path} returned HTTP {resp.status}: "
                              f"{_short(body) if body is not None else 'non-JSON body'}")
        return resp

    def payload(self, **fields):
        """POST /bundles body. Carries the session ID so the system can attribute the request."""
        if self.session_id:
            fields["session_id"] = self.session_id
        return fields

    def fixture(self, key):
        return self.oracle["releases"][key]

    def create_bundle(self, product_id, release_version):
        """POST a bundle request, then poll GET until a terminal status or the timeout."""
        run = BundleRun()
        resp = self.request("POST", "/bundles",
                            json_body=self.payload(product_id=product_id, release_version=release_version))
        run.post_status = resp.status
        run.post_body = resp.json()
        if resp.status != 202 or not isinstance(run.post_body, dict) or not run.post_body.get("bundle_id"):
            return run
        run.bundle_id = str(run.post_body["bundle_id"])

        deadline = time.monotonic() + self.poll_timeout
        while True:
            resp = self.request("GET", f"/bundles/{run.bundle_id}")
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
        try:
            return {
                category: (folder / self.oracle["files"][category]).read_bytes()
                for category, status in fx["categories"].items()
                if status == "PRESENT"
            }
        except OSError as exc:
            raise TesterAbort(f"The tester's own fixture files are unreadable: {exc}")

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
            resp = self.request("GET", url)
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
           ["REQ-01"], ["request-intake"])
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


@criterion("API-02", "POST /bundles rejects requests missing a required field with 400",
           ["REQ-01"], ["request-intake"])
def api_required_fields(ctx, case):
    payloads = [
        ("missing product_id", ctx.payload(release_version="v1.2")),
        ("missing release_version", ctx.payload(product_id="prd-88")),
        ("neither identifier", ctx.payload()),
    ]
    for label, payload in payloads:
        resp = ctx.request("POST", "/bundles", json_body=payload)
        body = resp.json()
        case.check(f"{label}: 400 with an 'error' message",
                   resp.status == 400 and isinstance(body, dict) and "error" in body,
                   f"HTTP {resp.status}: {_short(body if body is not None else resp.body.decode('utf-8', 'replace'))}")


@criterion("API-03", "POST /bundles enforces field constraints (non-empty strings, JSON body)",
           ["REQ-01"], ["request-intake"])
def api_field_constraints(ctx, case):
    payloads = [
        ("empty product_id", ctx.payload(product_id="", release_version="v1.2")),
        ("blank release_version", ctx.payload(product_id="prd-88", release_version="   ")),
        ("non-string product_id", ctx.payload(product_id=88, release_version="v1.2")),
    ]
    for label, payload in payloads:
        resp = ctx.request("POST", "/bundles", json_body=payload)
        case.check(f"{label}: 400", resp.status == 400, f"HTTP {resp.status}")
    resp = ctx.request("POST", "/bundles", raw_body=b"not json",
                       headers={"Content-Type": "text/plain"})
    case.check("non-JSON body: 4xx client error", 400 <= resp.status < 500, f"HTTP {resp.status}")


@criterion("API-04", "GET /bundles/{bundle_id} returns 404 for an unknown bundle",
           ["REQ-04"], ["status-retrieval"])
def api_unknown_bundle(ctx, case):
    resp = ctx.request("GET", f"/bundles/{uuid.uuid4()}")
    case.check("unknown UUID: 404", resp.status == 404, f"HTTP {resp.status}")


@criterion("API-05", "GET /bundles/{bundle_id} conforms to BundleStatusResponse",
           ["REQ-03", "REQ-04", "REQ-05"], ["status-retrieval", "manifest-generation"])
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


@criterion("API-06", "A bundle reaches a terminal status (COMPLETED, PARTIAL_SUCCESS, FAILED)",
           ["REQ-05"], ["manifest-generation"])
def api_terminal_status(ctx, case):
    run = ctx.bundle(case, "complete")
    case.check(f"terminal status reached within {ctx.poll_timeout}s", run.reached_terminal,
               f"last status {run.body.get('status')!r}")


@criterion("API-07", "An unknown release is not reported as collected",
           ["REQ-01", "REQ-06"], ["request-intake", "artifact-retrieval"])
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
           ["REQ-01", "REQ-04"], ["artifact-retrieval"])
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
           ["REQ-02", "REQ-03", "REQ-05"], ["artifact-retrieval", "classification", "manifest-generation"])
def ac02_complete_collection(ctx, case):
    fx = ctx.fixture("complete")
    run = ctx.bundle(case, "complete")
    check_categories(case, run, fx["categories"])
    case.check(f"bundle status is {fx['expected_bundle_status']}",
               run.body.get("status") == fx["expected_bundle_status"],
               f"got {run.body.get('status')!r}")


@criterion("AC-03", "Missing required artifact is reported as MISSING",
           ["REQ-03", "REQ-06"], ["classification"])
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
           ["REQ-03", "REQ-06"], ["classification"])
def ac04_invalid_artifact(ctx, case):
    fx = ctx.fixture("invalid")
    run = ctx.bundle(case, "invalid")
    check_categories(case, run, fx["categories"])
    case.check("bundle is not reported as COMPLETED", run.body.get("status") != "COMPLETED",
               f"got {run.body.get('status')!r}")
    for category, status in fx["categories"].items():
        if status != "PRESENT":
            check_not_collected(case, ctx, run, category)


@criterion("AC-05", "External source failure is reported as INACCESSIBLE",
           ["REQ-02", "REQ-06", "REQ-07"], ["source-failure-handling"])
def ac05_source_failure(ctx, case):
    raise Skip("Not executed: Phase 3 uses a single controlled source, so a source failure cannot be "
               "induced through the shared interface. Deferred to Phase 4 (Phase 2 section 6.1).")


@criterion("AC-06", "Source fidelity: collected content is byte-for-byte identical to the source",
           ["REQ-05", "REQ-08"], ["content-preservation"])
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
           ["REQ-04", "REQ-05", "REQ-08"], ["manifest-generation", "content-preservation"])
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
           ["REQ-03", "REQ-05"], ["classification", "manifest-generation"])
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


# --- Report -------------------------------------------------------------------

def _result(crit, outcome, message, source=None, checks=(), duration_ms=0.0):
    result = {"test_id": crit.id, "outcome": outcome}
    if outcome == "error":
        result["source"] = source or "unknown"
    result.update({
        "message": message,
        "title": crit.title,
        "requirements": crit.requirements,
        "components": crit.components,
        "checks": list(checks),
        "duration_ms": duration_ms,
    })
    return result


def _verdict_message(crit, checks):
    failed = [c for c in checks if c["passed"] is False]
    if failed:
        details = "; ".join(f"{c['description']}" + (f" ({c['detail']})" if c["detail"] else "")
                            for c in failed)
        return f"{crit.title}. {len(failed)} of {len(checks)} checks failed: {details}"
    observed = sum(c["passed"] is True for c in checks)
    na = sum(c["passed"] is None for c in checks)
    extra = f"; {na} not observable through this interface" if na else ""
    return f"{crit.title}. All observable checks passed ({observed}){extra}."


def _measure(covered, total, basis):
    return {"covered": covered, "total": total, "basis": basis}


def _valid_measure(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("basis"), str) or not entry["basis"].strip():
        return False
    covered, total = entry.get("covered"), entry.get("total")
    if covered is None and total is None:
        return True
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (covered, total))
    return ints and 0 <= covered <= total


def fetch_code_coverage(client, session_id):
    """coverage.code from the system's GET /test/report, or null counts explaining why not."""
    def unavailable(why):
        return _measure(None, None, f"Source lines; code coverage unavailable: {why}")

    if not session_id:
        return unavailable("no test session was prepared on the target")
    try:
        resp = client.request("GET", f"/test/report?session_id={quote(session_id, safe='')}",
                              timeout=COVERAGE_TIMEOUT)
    except NETWORK_ERRORS as exc:
        return unavailable(f"the target's /test/report could not be reached ({exc})")
    body = resp.json()
    if resp.status != 200 or not isinstance(body, dict):
        return unavailable(f"the target's /test/report returned HTTP {resp.status}")
    code = (body.get("coverage") or {}).get("code") if isinstance(body.get("coverage"), dict) else None
    if not _valid_measure(code):
        return unavailable(f"the target's /test/report returned a malformed coverage.code: {_short(code)}")
    return _measure(code.get("covered"), code.get("total"), code["basis"])


def compute_coverage(oracle, results, code):
    """Component and system coverage count what executed tests exercised, not whether they passed."""
    executed = [r for r in results if r["outcome"] in ("pass", "fail")]
    components = oracle.get("components") or {}
    criteria = oracle.get("acceptance_criteria") or []

    if components:
        hit = {c for r in executed for c in r["components"]} & set(components)
        component = _measure(len(hit), len(components),
                             "Components listed in the pod specification (oracle.json 'components'); a "
                             "component counts as covered when at least one executed test exercises it")
    else:
        component = _measure(None, None, "Component list unavailable: the tester could not load oracle.json")

    if criteria:
        hit = {r["test_id"] for r in executed} & set(criteria)
        system = _measure(len(hit), len(criteria),
                          "Acceptance criteria AC-01..AC-08 from the pod specification (Phase 2 section 4.1); "
                          "a criterion counts as covered when its test was executed (pass or fail)")
    else:
        system = _measure(None, None, "Acceptance criteria unavailable: the tester could not load oracle.json")

    return {"code": code, "component": component, "system": system}


def run_suite(client, session_id=None, target=None, oracle_path=DEFAULT_ORACLE, fixtures_root=DEFAULT_FIXTURES,
              poll_timeout=5.0, poll_interval=0.25, only=None, time_budget=RUN_BUDGET):
    """Run every criterion (or those whose id is in `only`) and return the suite report.

    Never raises for target problems: those become error results. If the tester itself
    cannot continue, the report has "aborted": true, the interrupted test is an error
    with source "tester" and every later test is a skip.
    """
    started = datetime.now(timezone.utc)
    t_start = time.monotonic()
    planned = [c for c in CRITERIA if not only or c.id in only]
    results = []
    abort_message = None

    try:
        oracle = json.loads(Path(oracle_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        oracle = {}
        abort_message = f"The tester could not load its expected results ({exc})"
    ctx = Context(client, oracle, fixtures_root, poll_timeout, poll_interval, session_id, time_budget)

    for crit in planned:
        if abort_message:
            results.append(_result(crit, "skip", f"Not executed: the tester aborted. {abort_message}"))
            continue
        case = Case()
        outcome = source = message = None
        t0 = time.monotonic()
        try:
            crit.fn(ctx, case)
        except Skip as exc:
            outcome, message = "skip", str(exc)
        except Abort:
            pass
        except TesterAbort as exc:
            abort_message = str(exc)
            outcome, source, message = "error", "tester", f"Prevented by a tester abort: {exc}"
        except CommunicationError as exc:
            outcome, source, message = "error", "communication", str(exc)
        except TargetError as exc:
            outcome, source, message = "error", "system", f"The target reported an internal failure: {exc}"
        except Exception as exc:  # a defect in this test's own code
            outcome, source, message = "error", "tester", f"Tester failure: {type(exc).__name__}: {exc}"

        if outcome is None:
            verdicts = [c["passed"] for c in case.checks]
            if any(v is False for v in verdicts):
                outcome = "fail"
            elif any(v is True for v in verdicts):
                outcome = "pass"
            else:
                outcome, message = "skip", "Not evaluated: no check was observable through this interface."
            message = message or _verdict_message(crit, case.checks)

        results.append(_result(crit, outcome, message, source, case.checks,
                               round((time.monotonic() - t0) * 1000, 1)))

    code = fetch_code_coverage(client, session_id)
    summary = {key: sum(r["outcome"] == key for r in results) for key in OUTCOMES}
    summary["total"] = len(results)
    report = {
        "session_id": session_id,
        "target_url": target,
        "results": results,
        "coverage": compute_coverage(oracle, results, code),
        "summary": summary,
        "passed": summary["fail"] == 0 and summary["error"] == 0 and abort_message is None,
        "spec_version": oracle.get("spec_version"),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.monotonic() - t_start) * 1000, 1),
    }
    if abort_message:
        report["aborted"] = True
        report["abort_message"] = abort_message
    return report


def aborted_report(session_id, target, message, only=None):
    """Report for a run that crashed before run_suite could return: every planned test is a skip."""
    planned = [c for c in CRITERIA if not only or c.id in only]
    results = [_result(c, "skip", f"Not executed: the tester aborted. {message}") for c in planned]
    unavailable = "unavailable: the tester aborted before measuring"
    return {
        "session_id": session_id,
        "target_url": target,
        "results": results,
        "coverage": {key: _measure(None, None, f"{basis}; {unavailable}") for key, basis in
                     (("code", "Source lines"), ("component", "Pod components"),
                      ("system", "Pod acceptance criteria"))},
        "summary": {**{key: 0 for key in OUTCOMES}, "skip": len(results), "total": len(results)},
        "passed": False,
        "aborted": True,
        "abort_message": message,
    }
