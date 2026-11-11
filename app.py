import copy
import io
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Flask, jsonify, request, send_file, url_for
from bundle_service import archives, process_bundle_safely
from github_client import GitHubConfig, GitHubError
from selftest import create_test_blueprint

VERSION_FILE = Path(__file__).parent / "VERSION"

app = Flask(__name__)
bundles: dict[str, dict] = {}
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

    if not isinstance(data, dict) or not data:
        return jsonify({"error": "Request body must contain JSON"}), 400

    if not isinstance(data.get("product_id"), str) or not data["product_id"].strip():
        return jsonify({"error": "product_id is required and must be a nonempty string"}), 400

    if not isinstance(data.get("release_version"), str) or not data["release_version"].strip():
        return jsonify({"error": "release_version is required and must be a nonempty string"}), 400

    github = None
    if "github" in data:
        source = data["github"]
        if not isinstance(source, dict):
            return jsonify({"error": "github must be an object"}), 400
        repository, revision = source.get("repository"), source.get("revision")
        try:
            # Validate the repository syntax without reading server credentials.
            GitHubConfig(token="", repository=repository, allow_anonymous=True)
        except GitHubError:
            return jsonify({"error": "github.repository must be in owner/repo form"}), 400
        if not isinstance(revision, str) or not revision or any(
            c.isspace() or ord(c) < 32 or ord(c) == 127 for c in revision
        ):
            return jsonify({"error": "github.revision must be a nonempty Git tag or commit"}), 400
        for field in ("product_id", "release_version"):
            value = data[field]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) or ".." in value:
                return jsonify({"error": f"{field} must be a safe path segment for GitHub"}), 400
        github = {"repository": repository, "revision": revision}

    bundle_id = str(uuid.uuid4())

    bundle = {
        "bundle_id": bundle_id,
        "product_id": data["product_id"],
        "release_version": data["release_version"],
        "status": "PENDING",
        "manifest": [],
        "artifacts": []
    }
    if github is not None:
        bundle["github"] = github

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
    if bundle_id not in bundles:
        return jsonify({"error": "Bundle not found"}), 404

    body = dict(bundles[bundle_id])
    if bundle_id in archives:
        # Absolute, so it still resolves when the app is mounted under /system (wsgi.py)
        body["download_url"] = url_for("download_bundle", bundle_id=bundle_id, _external=True)
    return jsonify(body), 200

@app.route("/bundles/<bundle_id>/download", methods=["GET"])
def download_bundle(bundle_id):
    # FAILED bundles have no archive, so they 404 here too
    if bundle_id not in archives:
        return jsonify({"error": "Bundle archive not found"}), 404

    return send_file(io.BytesIO(archives[bundle_id]), mimetype="application/zip",
                     as_attachment=True, download_name=f"bundle-{bundle_id}.zip")

if __name__ == "__main__":
    app.run(debug=True)  # local development only; production runs gunicorn via wsgi.py  # nosec B201
