from pathlib import Path

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
# Reported as external_source on every manifest item; Phase 3 has this one controlled source
SOURCE_NAME = "fixtures"

# category name -> the file we expect to find for it
EXPECTED_FILES = {
    "SBOM": "sbom.md",
    "Risk Assessment": "risk_assessment.md",
    "Test Logs": "test_logs.md",
    "User Instructions": "user_instructions.md",
}


def classify_file(path):
    """Return (status, content, source_failure_detail) for one expected file."""
    if not path.is_file():
        return "MISSING", None, None
    try:
        data = path.read_bytes()
    except OSError as exc:
        # REQ-07: the source failed for this artifact; the other categories still go ahead
        return "INACCESSIBLE", None, f"Could not read {path.name}: {exc.strerror or exc}"
    try:
        # Decoding bytes keeps line endings as they are, unlike read_text() (REQ-08)
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return "INVALID", None, f"{path.name} is not valid UTF-8 text"
    if content.strip() == "":
        return "INVALID", None, f"{path.name} is empty"
    return "PRESENT", content, None


def retrieve_and_classify(bundle):
    product = bundle["product_id"]
    release = bundle["release_version"]

    # Safety: don't let someone send "../../something" as a version
    if "/" in product or "/" in release or ".." in product or ".." in release:
        bundle["status"] = "FAILED"
        bundle["error"] = "Invalid product_id or release_version"
        return bundle

    folder = FIXTURES_ROOT / product / release
    if not folder.is_dir():
        bundle["status"] = "FAILED"
        bundle["error"] = f"No documents found for {product} {release}"
        return bundle

    artifacts = []
    manifest = []
    for category, filename in EXPECTED_FILES.items():
        status, content, detail = classify_file(folder / filename)
        item = {"category": category, "status": status, "external_source": SOURCE_NAME}
        if detail:
            item["source_failure_detail"] = detail
        manifest.append(item)
        if status == "PRESENT":
            artifacts.append({
                "category": category,
                "product_id": product,
                "release_version": release,
                "content": content,
            })

    bundle["artifacts"] = artifacts
    bundle["manifest"] = manifest
    bundle["status"] = "PROCESSING"
    return bundle
