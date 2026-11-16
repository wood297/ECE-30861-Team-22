import copy
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, jsonify, request, send_file, url_for
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
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body must contain JSON"}), 400

    if not isinstance(data.get("product_id"), str) or not data["product_id"].strip():
        return jsonify({"error": "product_id is required and must be a nonempty string"}), 400

    if not isinstance(data.get("release_version"), str) or not data["release_version"].strip():
        return jsonify({"error": "release_version is required and must be a nonempty string"}), 400

    bundle_id = str(uuid.uuid4())

    bundle = {
        "bundle_id": bundle_id,
        "product_id": data["product_id"],
        "release_version": data["release_version"],
        "status": "PENDING",
        "manifest": [],
        "artifacts": []
    }

    bundles[bundle_id] = bundle
    # Spec 5.1: the 202 acknowledges the queued task as PENDING; results come from GET
    acknowledgement = copy.deepcopy(bundle)

    if app.config.get("PROCESS_BUNDLES_INLINE"):
        process_bundle_safely(bundle)
    else:
        executor.submit(process_bundle_safely, bundle)

    return jsonify(acknowledgement), 202

@app.route("/bundles/<bundle_id>", methods=["GET"])
def get_bundle(bundle_id):
    # One lookup each, so an entry expiring between a check and a read can't cause a KeyError
    bundle = bundles.get(bundle_id)
    if bundle is None:
        return jsonify({"error": "Bundle not found"}), 404

    body = dict(bundle)
    if archives.get(bundle_id) is not None:
        # Absolute, so it still resolves when the app is mounted under /system (wsgi.py)
        body["download_url"] = url_for("download_bundle", bundle_id=bundle_id, _external=True)
    return jsonify(body), 200

@app.route("/bundles/<bundle_id>/download", methods=["GET"])
def download_bundle(bundle_id):
    # FAILED bundles have no archive, so they 404 here too
    archive = archives.get(bundle_id)
    if archive is None:
        return jsonify({"error": "Bundle archive not found"}), 404

    return send_file(io.BytesIO(archive), mimetype="application/zip",
                     as_attachment=True, download_name=f"bundle-{bundle_id}.zip")

if __name__ == "__main__":
    app.run(debug=True)  # local development only; production runs gunicorn via wsgi.py  # nosec B201
