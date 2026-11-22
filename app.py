import copy
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, g, jsonify, request, send_file, session, url_for
import auth
import rate_limits
from artifact_pipeline import CompanyAccount
from bundle_service import BUNDLE_TTL_SECONDS, MAX_BUNDLE_BYTES, MAX_BUNDLES, archives, process_bundle_safely
from expiring_store import ExpiringStore
from selftest import create_test_blueprint

VERSION_FILE = Path(__file__).parent / "VERSION"

# Largest request body accepted; bigger ones get 413 before being read into memory
MAX_REQUEST_BYTES = 1024 * 1024


def bundle_size(bundle: dict) -> int:
    """Approximate memory a bundle holds: its artifact content plus a little for the rest."""
    return 1024 + sum(len(artifact.get("content") or "") for artifact in bundle.get("artifacts") or [])


def drop_archive(bundle_id: str, _bundle: dict) -> None:
    archives.pop(bundle_id)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
# Bundles expire after an hour, and the oldest go first past the caps. Removing a bundle also
# removes its archive, so a download never outlives the bundle it belongs to.
bundles: ExpiringStore[str, dict] = ExpiringStore(
    BUNDLE_TTL_SECONDS, MAX_BUNDLES, max_bytes=MAX_BUNDLE_BYTES, size_of=bundle_size,
    on_evict=drop_archive)
# Classifying a bundle can take a while, so requests don't wait for it. Tests can set
# PROCESS_BUNDLES_INLINE to read results straight after the POST.
executor = ThreadPoolExecutor(max_workers=4)
app.register_blueprint(create_test_blueprint())
# Customer login (Phase 5 plan §4). Off, with every request acting as pod-test, until the Cognito
# settings and app secrets are present
auth.init_app(app, auth.load_from_env())

def public(bundle: dict) -> dict:
    """A bundle as callers see it: internal keys (an "_" prefix, e.g. its owner) left out."""
    return {key: value for key, value in bundle.items() if not key.startswith("_")}


def requesting_org(url_org_id: str | None = None):
    """(org_id, None) for a company's bundle, (None, None) for pod-test, or (None, error response).

    No login is pod-test (§4). A signed-in browser uses its active organization; an API caller
    names the organization in the URL (POST /orgs/<org_id>/bundles). Membership is checked here,
    never assumed from the request."""
    state = app.extensions.get("cra_auth")
    identity = g.identity
    if state is None:
        if url_org_id is not None:
            return None, (jsonify({"error": "Accounts aren't available on this server"}), 503)
        return None, None
    if identity.is_tester:
        if url_org_id is not None:
            return None, (jsonify({"error": "Sign in to request bundles for an organization"}), 401)
        return None, None
    org_id = url_org_id if url_org_id is not None else (session.get("org_id") if identity.via == "session" else None)
    if not org_id:
        return None, (jsonify({"error": "Choose an organization: POST /orgs/<org_id>/bundles, "
                                        "or pick one on the organizations page"}), 400)
    if state.accounts.member(org_id, identity.sub) is None:
        return None, (jsonify({"error": "Organization not found"}), 404)
    return org_id, None


def bundle_rate_limited(org_id: str | None) -> float | None:
    """Count one bundle request against its limit (rate_limits.py): per organization for a company,
    per IP address for pod-test. None if allowed, else seconds to wait."""
    if org_id is not None:
        return rate_limits.org_bundles.hit(org_id)
    return rate_limits.pod_test_bundles.hit(request.remote_addr or "unknown")


def forget_org_bundles(org_id: str) -> int:
    """Remove a deleted organization's bundles and archives from memory."""
    owned = [bundle_id for bundle_id in list(bundles) if (bundles.get(bundle_id) or {}).get("_org_id") == org_id]
    for bundle_id in owned:
        bundles.pop(bundle_id)
        archives.pop(bundle_id)
    return len(owned)


def may_read(bundle: dict) -> bool:
    """pod-test bundles are readable by anyone with the ID (§5). A company's only by its members;
    everyone else gets the same 404 as an unknown ID."""
    owner = bundle.get("_org_id")
    if owner is None:
        return True
    state = app.extensions.get("cra_auth")
    identity = g.identity
    return state is not None and not identity.is_tester and state.accounts.member(owner, identity.sub) is not None


@app.route("/", methods=["GET"])
def index():
    # The base URL is what other teams register, so make it say what lives under it
    return jsonify({"service": "Team 22 CRA documentation system", "endpoints": [
        "GET  /health",
        "GET  /version",
        "POST /bundles",
        "GET  /bundles/<bundle_id>",
        "GET  /bundles/<bundle_id>/download",
        "POST /test/setup",
        "GET  /test/report",
    ]}), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/version", methods=["GET"])
def version():
    # Written at deploy time (deploy/build_release.sh), so a local run has no version
    version_text = VERSION_FILE.read_text().strip() if VERSION_FILE.is_file() else "unknown"
    return jsonify({"version": version_text}), 200

@app.route("/bundles", methods=["POST"])
def create_bundle():
    return start_bundle(None)


@app.route("/orgs/<org_id>/bundles", methods=["POST"])
def create_org_bundle(org_id):
    return start_bundle(org_id)


def start_bundle(url_org_id):
    org_id, refused = requesting_org(url_org_id)
    if refused is not None:
        return refused
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body must contain JSON"}), 400

    if not isinstance(data.get("product_id"), str) or not data["product_id"].strip():
        return jsonify({"error": "product_id is required and must be a nonempty string"}), 400

    if not isinstance(data.get("release_version"), str) or not data["release_version"].strip():
        return jsonify({"error": "release_version is required and must be a nonempty string"}), 400

    wait = bundle_rate_limited(org_id)
    if wait is not None:
        limit = rate_limits.ORG_BUNDLES_PER_MINUTE if org_id else rate_limits.POD_TEST_BUNDLES_PER_MINUTE
        response = jsonify({"error": f"Too many bundle requests: at most {limit} a minute. Try again shortly."})
        response.headers["Retry-After"] = str(max(1, int(wait + 0.999)))
        return response, 429

    # Spec 5.1: the 202 acknowledges the queued task as PENDING; results come from GET
    return jsonify(submit_bundle(data["product_id"], data["release_version"], org_id)), 202


def submit_bundle(product_id: str, release_version: str, org_id: str | None) -> dict:
    """Queue a bundle, for pod-test (org_id None) or a company, and return its PENDING
    acknowledgement. The bundle API and the company pages both start bundles here."""
    bundle_id = str(uuid.uuid4())

    bundle = {
        "bundle_id": bundle_id,
        "product_id": product_id,
        "release_version": release_version,
        "status": "PENDING",
        "manifest": [],
        "artifacts": []
    }

    company = None
    if org_id is not None:
        state = app.extensions["cra_auth"]
        bundle["_org_id"] = org_id
        company = CompanyAccount(org_id, state.accounts.store, state.secrets)

    bundles[bundle_id] = bundle
    acknowledgement = copy.deepcopy(public(bundle))

    if app.config.get("PROCESS_BUNDLES_INLINE"):
        process_bundle_safely(bundle, company)
    else:
        executor.submit(process_bundle_safely, bundle, company)

    return acknowledgement

@app.route("/bundles/<bundle_id>", methods=["GET"])
def get_bundle(bundle_id):
    # One lookup each, so an entry expiring between a check and a read can't cause a KeyError
    bundle = bundles.get(bundle_id)
    if bundle is None or not may_read(bundle):
        return jsonify({"error": "Bundle not found"}), 404

    body = public(bundle)
    if archives.get(bundle_id) is not None:
        # Absolute, so it still resolves when the app is mounted under /system (wsgi.py)
        body["download_url"] = url_for("download_bundle", bundle_id=bundle_id, _external=True)
    return jsonify(body), 200

@app.route("/bundles/<bundle_id>/download", methods=["GET"])
def download_bundle(bundle_id):
    # FAILED bundles have no archive, so they 404 here too
    archive = archives.get(bundle_id)
    bundle = bundles.get(bundle_id)
    if archive is None or bundle is None or not may_read(bundle):
        return jsonify({"error": "Bundle archive not found"}), 404

    return send_file(io.BytesIO(archive), mimetype="application/zip",
                     as_attachment=True, download_name=f"bundle-{bundle_id}.zip")

if __name__ == "__main__":
    app.run(debug=True)  # local development only; production runs gunicorn via wsgi.py  # nosec B201
