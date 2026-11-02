import io
import json
import logging
import zipfile

from artifact_pipeline import retrieve_and_classify

logger = logging.getLogger(__name__)

# bundle_id -> zip archive (bytes) of the generated bundle, served by GET /bundles/<id>/download.
# Kept in memory for Phase 3, so a restart clears it along with the bundles.
archives: dict[str, bytes] = {}

# Every entry gets the same timestamp, so identical inputs give byte-identical archives (AC-07)
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def final_status(manifest):
    """COMPLETED only when every category is PRESENT (spec 3.3); otherwise PARTIAL_SUCCESS."""
    if all(item["status"] == "PRESENT" for item in manifest):
        return "COMPLETED"
    return "PARTIAL_SUCCESS"


def build_archive(bundle):
    """Zip of the collected artifacts under their source file names, plus manifest.json (REQ-05).

    Artifact bytes are written exactly as retrieved (REQ-08). manifest.json leaves out the
    bundle_id so that two bundles for the same release produce the same archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in bundle["artifacts"]:
            _add(archive, artifact["source_file"], artifact["content"].encode("utf-8"))
        manifest = {key: bundle[key] for key in ("product_id", "release_version", "status", "manifest")}
        _add(archive, "manifest.json", json.dumps(manifest, indent=2).encode("utf-8") + b"\n")
    return buffer.getvalue()


def _add(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=ARCHIVE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def process_bundle(bundle):
    """
    Processes a bundle request.

    Person 2: retrieve and classify artifacts.
    Person 3: generate the final bundle and manifest.
    """

    # This can run in the background while GET reads the same bundle, so the terminal status
    # is set last: a client that sees it can rely on the manifest and archive being ready.
    bundle["status"] = "PROCESSING"

    # Person 2: fills in bundle["artifacts"] and bundle["manifest"]
    retrieve_and_classify(bundle)
    if bundle["status"] == "FAILED":
        return bundle

    # Person 3: terminal status, then the downloadable archive
    status = final_status(bundle["manifest"])
    archives[bundle["bundle_id"]] = build_archive({**bundle, "status": status})
    bundle["status"] = status

    return bundle


def process_bundle_safely(bundle):
    """Runs process_bundle, making sure a bundle can't stay stuck if something unexpected breaks."""
    try:
        process_bundle(bundle)
    except Exception:
        logger.exception("Processing failed for bundle %s", bundle["bundle_id"])
        bundle["error"] = "Processing failed unexpectedly"
        bundle["status"] = "FAILED"
