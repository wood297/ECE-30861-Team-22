import io
import json
import logging
import zipfile

from artifact_pipeline import retrieve_and_classify
from expiring_store import ExpiringStore

logger = logging.getLogger(__name__)

# How long a bundle and its archive stay available, and how many can exist at once. Anyone can
# create bundles without logging in, so these keep memory bounded (Phase 5 plan, step 1.1).
BUNDLE_TTL_SECONDS = 3600
MAX_BUNDLES = 500
MAX_BUNDLE_BYTES = 64 * 1024 * 1024

# bundle_id -> zip archive (bytes) of the generated bundle, served by GET /bundles/<id>/download.
# Kept in memory, so a restart clears it along with the bundles. app.py also removes an archive
# when its bundle is removed.
archives: ExpiringStore[str, bytes] = ExpiringStore(BUNDLE_TTL_SECONDS, MAX_BUNDLES,
                                                    max_bytes=MAX_BUNDLE_BYTES, size_of=len)

# Every entry gets the same timestamp, so identical inputs give byte-identical archives (AC-07)
ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def final_status(manifest):
    """COMPLETED only when every category is PRESENT (spec 3.3); otherwise PARTIAL_SUCCESS."""
    if all(item["status"] == "PRESENT" for item in manifest):
        return "COMPLETED"
    return "PARTIAL_SUCCESS"


MANIFEST_NAME = "manifest.json"


def archive_names(artifacts):
    """The path of each artifact in the zip: its source file name, or <category>/<name> when that
    name is taken by an earlier artifact or by manifest.json. Files from different sources (two
    connectors, say) can share a name, and a zip must not hold two entries with one name."""
    used = {MANIFEST_NAME}
    names = []
    for artifact in artifacts:
        name = artifact["source_file"]
        if name in used:
            name = f"{artifact['category'].lower().replace(' ', '-')}/{name}"
        used.add(name)
        names.append(name)
    return names


def build_archive(bundle):
    """Zip of the collected artifacts under their source file names, plus manifest.json (REQ-05).

    Artifact bytes are written exactly as retrieved (REQ-08). manifest.json leaves out the
    bundle_id so that two bundles for the same release produce the same archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact, name in zip(bundle["artifacts"], archive_names(bundle["artifacts"])):
            _add(archive, name, artifact["content"].encode("utf-8"))
        manifest = {key: bundle[key] for key in ("product_id", "release_version", "status", "manifest")}
        _add(archive, MANIFEST_NAME, json.dumps(manifest, indent=2).encode("utf-8") + b"\n")
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
