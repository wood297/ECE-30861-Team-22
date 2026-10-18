import copy
import io
import uuid
from flask import Flask, jsonify, request, send_file, url_for
from bundle_service import archives, process_bundle
from selftest import create_test_blueprint

app = Flask(__name__)
bundles = {}
app.register_blueprint(create_test_blueprint())

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

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

    process_bundle(bundle)

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
    app.run(debug=True)