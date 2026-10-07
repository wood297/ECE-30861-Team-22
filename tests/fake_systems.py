"""Minimal in-memory systems used to check that the acceptance suite itself is correct.

make_reference_system() implements the shared spec fully, so every criterion should
pass against it. Each defect switches on one deliberate spec violation, which the
corresponding criterion must catch.
"""
import io
import json
import uuid
import zipfile

from flask import Flask, jsonify, request

from test_runner.suite import DEFAULT_FIXTURES, DEFAULT_ORACLE

FILES = json.loads(DEFAULT_ORACLE.read_text(encoding="utf-8"))["files"]

DEFECTS = {
    "lax_validation",    # accepts empty / non-string identifiers
    "leak_release",      # always serves v1.2 artifacts, whatever release was requested
    "alter_content",     # strips trailing whitespace from artifacts
    "accept_empty",      # treats an empty artifact as PRESENT
    "drop_missing",      # leaves MISSING categories out of the manifest
    "always_completed",  # reports every bundle as COMPLETED
    "zip_only",          # exposes content only through a zip download_url (not a defect)
}


def make_reference_system(*defects):
    unknown = set(defects) - DEFECTS
    assert not unknown, f"unknown defects: {unknown}"

    app = Flask("reference_system")
    store = {}
    archives = {}

    @app.route("/bundles", methods=["POST"])
    def create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "JSON object required"}), 400
        for field in ("product_id", "release_version"):
            if field not in data:
                return jsonify({"error": f"Missing required field: '{field}'"}), 400
            value = data[field]
            if "lax_validation" not in defects and (not isinstance(value, str) or not value.strip()):
                return jsonify({"error": f"'{field}' must be a non-empty string"}), 400

        product, release = str(data["product_id"]), str(data["release_version"])
        folder = DEFAULT_FIXTURES / product / ("v1.2" if "leak_release" in defects else release)

        manifest, artifacts = [], []
        for category, filename in FILES.items():
            path = folder / filename
            if not path.is_file():
                status = "MISSING"
            elif not path.read_bytes().strip() and "accept_empty" not in defects:
                status = "INVALID"
            else:
                status = "PRESENT"
            if status == "MISSING" and "drop_missing" in defects:
                continue
            manifest.append({"category": category, "status": status, "external_source": "fixtures"})
            if status == "PRESENT":
                content = path.read_bytes().decode("utf-8")
                if "alter_content" in defects:
                    content = content.rstrip()
                artifacts.append({"category": category, "product_id": product,
                                  "release_version": release, "content": content})

        if not folder.is_dir():
            status = "FAILED"
        elif all(item["status"] == "PRESENT" for item in manifest):
            status = "COMPLETED"
        else:
            status = "PARTIAL_SUCCESS"
        if "always_completed" in defects:
            status = "COMPLETED"

        bundle_id = str(uuid.uuid4())
        body = {"bundle_id": bundle_id, "product_id": product, "release_version": release,
                "status": status, "manifest": manifest}
        if "zip_only" in defects:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                for artifact in artifacts:
                    archive.writestr(f"{artifact['category']}.md", artifact["content"].encode("utf-8"))
            archives[bundle_id] = buffer.getvalue()
            body["download_url"] = f"/downloads/{bundle_id}.zip"
        else:
            body["artifacts"] = artifacts
        store[bundle_id] = body
        return jsonify({"bundle_id": bundle_id, "status": "PENDING"}), 202

    @app.route("/bundles/<bundle_id>", methods=["GET"])
    def get(bundle_id):
        if bundle_id not in store:
            return jsonify({"error": "Bundle not found"}), 404
        return jsonify(store[bundle_id]), 200

    @app.route("/downloads/<bundle_id>.zip", methods=["GET"])
    def download(bundle_id):
        if bundle_id not in archives:
            return jsonify({"error": "Bundle not found"}), 404
        return archives[bundle_id], 200, {"Content-Type": "application/zip"}

    return app
