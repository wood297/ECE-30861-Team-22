import uuid
from flask import Flask, jsonify, request
from bundle_service import process_bundle

app = Flask(__name__)
bundles = {}
test_sessions = {}

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

    process_bundle(bundle)

    return jsonify(bundle), 202

@app.route("/bundles/<bundle_id>", methods=["GET"])
def get_bundle(bundle_id):
    if bundle_id not in bundles:
        return jsonify({"error": "Bundle not found"}), 404

    return jsonify(bundles[bundle_id]), 200

@app.route("/test/setup", methods=["POST"])
def test_setup():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "message": "Request body must be a JSON object",
            "source": "system"
        }), 400

    session_id = data.get("session_id")
    setup = data.get("setup")

    if not isinstance(session_id, str) or not session_id.strip():
        return jsonify({
            "message": "session_id must be a nonempty string",
            "source": "system"
        }), 400

    if not isinstance(setup, dict):
        return jsonify({
            "message": "setup must be a JSON object",
            "source": "system"
        }), 400

    if session_id in test_sessions:
        return jsonify({
            "message": "Session already exists",
            "source": "system"
        }), 409

    test_sessions[session_id] = {
        "setup": setup,
        "ready": True
    }

    return jsonify({"session_id": session_id}), 201

@app.route("/test/report", methods=["GET"])
def test_report():
    session_id = request.args.get("session_id")

    if not session_id:
        return jsonify({
            "message": "session_id is required",
            "source": "system"
        }), 400

    if session_id not in test_sessions:
        return jsonify({
            "message": "Session not found",
            "source": "system"
        }), 404

    return jsonify({
        "session_id": session_id,
        "coverage": {
            "code": {
                "covered": None,
                "total": None,
                "basis": "Source lines; target instrumentation unavailable"
            }
        }
    }), 200

if __name__ == "__main__":
    app.run(debug=True)