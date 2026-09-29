import uuid
from flask import Flask, jsonify, request
from bundle_service import process_bundle

app = Flask(__name__)
bundles = {}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/bundles", methods=["POST"])
def create_bundle():
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body must contain JSON"}), 400

    if "product_id" not in data:
        return jsonify({"error": "product_id is required"}), 400

    if "release_version" not in data:
        return jsonify({"error": "release_version is required"}), 400

    bundle_id = str(uuid.uuid4())

    bundle = {
        "bundle_id": bundle_id,
        "product_id": data["product_id"],
        "release_version": data["release_version"],
        "status": "PENDING",
        "categories": [],
        "artifacts": []
    }

    bundles[bundle_id] = bundle

    process_bundle(bundle)

    return jsonify(bundle), 202

@app.route("/bundles/<bundle_id>", methods=["GET"])
def get_bundle(bundle_id):
    if bundle_id not in bundles:
        return jsonify({"error": "Bundle not found"}), 404

    return jsonify(bundles[bundle_id]), 200

if __name__ == "__main__":
    app.run(debug=True)