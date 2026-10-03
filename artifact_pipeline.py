from pathlib import Path

FIXTURES_ROOT = Path(__file__).parent / "fixtures"

# category name -> the file we expect to find for it
EXPECTED_FILES = {
    "SBOM": "sbom.md",
    "Risk Assessment": "risk_assessment.md",
    "Test Logs": "test_logs.md",
    "User Instructions": "user_instructions.md",
}


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
        path = folder / filename
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if content.strip() == "":
                manifest.append({"category": category, "status": "INVALID"})
            else:
                manifest.append({"category": category, "status": "PRESENT"})
                artifacts.append({
                    "category": category,
                    "product_id": product,
                    "release_version": release,
                    "content": content,
                })
        else:
            manifest.append({"category": category, "status": "MISSING"})

    bundle["artifacts"] = artifacts
    bundle["manifest"] = manifest
    bundle["status"] = "PROCESSING"
    return bundle