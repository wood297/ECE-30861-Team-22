"""Unit tests for artifact retrieval and classification (REQ-02, REQ-03, REQ-04, REQ-06, REQ-08).

Each test builds its own release folder under tmp_path, so the shared fixtures can
change without breaking these tests.
"""
from pathlib import Path

import pytest

import artifact_pipeline
from artifact_pipeline import EXPECTED_FILES, retrieve_and_classify

ALL_FILES = {filename: f"# {category}\n" for category, filename in EXPECTED_FILES.items()}


@pytest.fixture
def fixtures_root(tmp_path, monkeypatch):
    root = tmp_path / "fixtures"
    root.mkdir()
    monkeypatch.setattr(artifact_pipeline, "FIXTURES_ROOT", root)
    return root


def make_release(root, files, product="prd-1", release="v1.0"):
    folder = root / product / release
    folder.mkdir(parents=True)
    for name, content in files.items():
        (folder / name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder


def classify(product="prd-1", release="v1.0"):
    return retrieve_and_classify({"product_id": product, "release_version": release,
                                  "status": "PENDING", "manifest": [], "artifacts": []})


def statuses(bundle):
    return {item["category"]: item["status"] for item in bundle["manifest"]}


def test_all_categories_present(fixtures_root):
    make_release(fixtures_root, ALL_FILES)

    bundle = classify()

    assert statuses(bundle) == {category: "PRESENT" for category in EXPECTED_FILES}
    assert sorted(a["category"] for a in bundle["artifacts"]) == sorted(EXPECTED_FILES)


def test_missing_file_is_missing_and_not_collected(fixtures_root):
    files = dict(ALL_FILES)
    del files[EXPECTED_FILES["Test Logs"]]
    make_release(fixtures_root, files)

    bundle = classify()

    assert statuses(bundle)["Test Logs"] == "MISSING"
    assert "Test Logs" not in [a["category"] for a in bundle["artifacts"]]


@pytest.mark.parametrize("content", ["", " \n\t\n"], ids=["empty", "whitespace-only"])
def test_blank_artifact_is_invalid_and_not_collected(fixtures_root, content):
    make_release(fixtures_root, {**ALL_FILES, EXPECTED_FILES["SBOM"]: content})

    bundle = classify()

    assert statuses(bundle)["SBOM"] == "INVALID"
    assert "SBOM" not in [a["category"] for a in bundle["artifacts"]]


def test_every_category_is_listed_once_even_for_an_empty_release(fixtures_root):
    make_release(fixtures_root, {})

    bundle = classify()

    assert [item["category"] for item in bundle["manifest"]] == list(EXPECTED_FILES)
    assert set(statuses(bundle).values()) == {"MISSING"}
    assert bundle["artifacts"] == []


def test_unrelated_files_are_ignored(fixtures_root):
    make_release(fixtures_root, {**ALL_FILES, "notes.txt": "scratch", "README.md": "# readme"})

    bundle = classify()

    assert len(bundle["manifest"]) == len(EXPECTED_FILES)
    assert len(bundle["artifacts"]) == len(EXPECTED_FILES)


def test_artifacts_are_tagged_with_their_release(fixtures_root):
    make_release(fixtures_root, ALL_FILES, product="prd-7", release="v2.1")

    bundle = classify("prd-7", "v2.1")

    assert {(a["product_id"], a["release_version"]) for a in bundle["artifacts"]} == {("prd-7", "v2.1")}


def test_releases_of_the_same_product_do_not_mix(fixtures_root):
    make_release(fixtures_root, {"sbom.md": "# SBOM v1\n"}, release="v1")
    make_release(fixtures_root, {"sbom.md": "# SBOM v2\n"}, release="v2")

    bundle = classify(release="v2")

    assert [a["content"] for a in bundle["artifacts"]] == ["# SBOM v2\n"]


def test_content_is_preserved_exactly(fixtures_root):
    content = "# SBOM\n\n| pkg | ver |\n|---|---|\n| zlib | 1.3.1 |\n\nFine: €15 million  \n\n"
    make_release(fixtures_root, {"sbom.md": content})

    bundle = classify()

    assert bundle["artifacts"][0]["content"] == content


def test_crlf_line_endings_are_preserved(fixtures_root):
    make_release(fixtures_root, {"sbom.md": b"# SBOM\r\nline two\r\n"})

    bundle = classify()

    assert bundle["artifacts"][0]["content"] == "# SBOM\r\nline two\r\n"


def test_non_utf8_artifact_is_invalid_rather_than_a_crash(fixtures_root):
    make_release(fixtures_root, {**ALL_FILES, "sbom.md": b"\xff\xfe\x00binary"})

    bundle = classify()

    assert statuses(bundle)["SBOM"] == "INVALID"
    assert "SBOM" not in [a["category"] for a in bundle["artifacts"]]


def test_unreadable_artifact_is_inaccessible_and_others_are_kept(fixtures_root, monkeypatch):
    make_release(fixtures_root, ALL_FILES)
    read_bytes = Path.read_bytes

    def fail_for_sbom(path):
        if path.name == "sbom.md":
            raise PermissionError(13, "Permission denied")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_for_sbom)

    bundle = classify()

    sbom = next(item for item in bundle["manifest"] if item["category"] == "SBOM")
    assert sbom["status"] == "INACCESSIBLE"
    assert sbom["source_failure_detail"] == "Could not read sbom.md: Permission denied"
    assert sorted(a["category"] for a in bundle["artifacts"]) == sorted(set(EXPECTED_FILES) - {"SBOM"})


def test_manifest_items_name_their_source_and_explain_failures(fixtures_root):
    files = {**ALL_FILES, "sbom.md": "", "test_logs.md": b"\xff"}
    del files["risk_assessment.md"]
    make_release(fixtures_root, files)

    items = {item["category"]: item for item in classify()["manifest"]}

    assert {item["external_source"] for item in items.values()} == {"fixtures"}
    assert items["SBOM"]["source_failure_detail"] == "sbom.md is empty"
    assert items["Test Logs"]["source_failure_detail"] == "test_logs.md is not valid UTF-8 text"
    assert "source_failure_detail" not in items["Risk Assessment"]
    assert "source_failure_detail" not in items["User Instructions"]


@pytest.mark.parametrize("product, release", [("prd-1", "v9.9"), ("no-such-product", "v1.0")])
def test_unknown_release_fails_without_artifacts(fixtures_root, product, release):
    make_release(fixtures_root, ALL_FILES)

    bundle = classify(product, release)

    assert bundle["status"] == "FAILED"
    assert "error" in bundle
    assert bundle["artifacts"] == []


@pytest.mark.parametrize("product, release", [
    ("..", "outside"),
    ("prd-1", "../../outside/secret"),
    ("prd-1", "v1.0/../v1.0"),
    ("/etc", "v1.0"),
])
def test_path_traversal_is_rejected(fixtures_root, product, release):
    make_release(fixtures_root, ALL_FILES)
    # A real folder next to the fixtures root that a traversal could otherwise reach
    make_release(fixtures_root.parent, ALL_FILES, product="outside", release="secret")

    bundle = classify(product, release)

    assert bundle["status"] == "FAILED"
    assert bundle["artifacts"] == []
