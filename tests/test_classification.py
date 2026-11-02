"""Tests for choosing each category's document and using Bedrock's answers (Phase 4 Plan §4).

A fake provider stands in for Bedrock, so nothing here reaches AWS. Each answer is set by a word
in the document, and every call is recorded so tests can check exactly when Bedrock is used.
"""
import io
import json
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

import app as app_module
import artifact_pipeline
import bundle_service
from app import app, bundles
from artifact_classifier import CATEGORIES
from artifact_formats import validate_artifact
from artifact_pipeline import SourceFile, decide, has_proper_heading

ROOT_FIXTURES = Path(__file__).parent.parent / "fixtures"
CYCLONEDX = json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6"}).encode()
NORMAL = {
    "sbom.md": b"# SBOM\n\npackages\n",
    "risk_assessment.md": b"# Risk Assessment\n\nthreats\n",
    "test_logs.md": b"# Test Logs\n\nresults\n",
    "user_instructions.md": b"# User Instructions\n\nguidance\n",
}
ANSWERS = {"packages": "SBOM", "threats": "Risk Assessment", "results": "Test Logs", "guidance": "User Instructions"}


class FakeProvider:
    model_id = "fake-model"

    def __init__(self, failure=None, evidence=None):
        self.documents = []
        self.failure = failure
        self.evidence = evidence

    def classify(self, *, system_prompt, artifact_json, response_schema):
        document = json.loads(artifact_json)["document"]
        self.documents.append((system_prompt, artifact_json))
        if self.failure:
            raise self.failure
        found = [(word, category) for word, category in ANSWERS.items() if word in document]
        if len(found) != 1:
            return json.dumps({"actual_category": None, "ambiguous": True, "evidence": document[:20]})
        word, category = found[0]
        return json.dumps({"actual_category": category, "ambiguous": False, "evidence": self.evidence or word})


def run(files, provider=None, **overrides):
    provider = FakeProvider() if provider is None else provider
    sources = [SourceFile(name, data=data) for name, data in {**files, **overrides}.items()]
    return decide(sources, provider), provider


def only(*names):
    return {name: NORMAL[name] for name in names}


def without(*names):
    return {name: data for name, data in NORMAL.items() if name not in names}


# Heading rule


@pytest.mark.parametrize("category, declared_format, text, expected", [
    ("SBOM", "markdown", "# SBOM: prd-88 v1.2\n", True),
    ("SBOM", "markdown", "# sbom\n", True),
    ("SBOM", "markdown", "﻿# SBOM\n", True),
    ("SBOM", "markdown", "# SBOM\r\nmore\r\n", True),
    ("SBOM", "markdown", "Intro text\n\n# SBOM\n", True),
    ("SBOM", "markdown", "No heading\n", False),
    ("SBOM", "markdown", "## SBOM\n", False),
    ("SBOM", "markdown", "#SBOM\n", False),
    ("SBOM", "markdown", "# User Instructions\n", False),
    ("SBOM", "markdown", "# Software Bill of Materials\n", False),
    ("SBOM", "markdown", "# Overview\n\n# SBOM\n", False),
    ("SBOM", "markdown", "```\n# SBOM\n```\ntext\n", False),
    ("Test Logs", "html", "<html><h1>Test Logs</h1></html>", True),
    ("Test Logs", "html", "<html><H1>test logs</H1></html>", True),
    ("Test Logs", "html", "<html><h1>Test <em>Logs</em></h1></html>", True),
    ("Test Logs", "html", "<html><h2>Test Logs</h2></html>", False),
    ("Test Logs", "html", "<html><h1>Other</h1><h1>Test Logs</h1></html>", False),
    ("SBOM", "cyclonedx-json", CYCLONEDX.decode(), True),
])
def test_heading_rule(category, declared_format, text, expected):
    validation = validate_artifact(text.encode(), declared_format)
    assert validation.valid
    assert has_proper_heading(validation, category) is expected


# Standard-named files


def test_proper_files_are_accepted_without_bedrock():
    decisions, provider = run(NORMAL)
    assert {decision.status for decision in decisions.values()} == {"PRESENT"}
    assert {decision.classified_by for decision in decisions.values()} == {"rules"}
    assert provider.documents == []


@pytest.mark.parametrize("name, data", [
    ("sbom.md", b""), ("sbom.md", b"\xff\xfe"), ("sbom.md", b'{"a": 1}'), ("sbom.json", b'{"not": "cyclonedx"}'),
    ("sbom.xml", b"<notbom/>"), ("sbom.html", b"plain text"), ("sbom.pdf", b"%PDF"),
])
def test_bad_standard_file_is_invalid_without_bedrock(name, data):
    decisions, provider = run(without("sbom.md"), **{name: data})
    assert decisions["SBOM"].status == "INVALID"
    assert decisions["SBOM"].detail
    assert provider.documents == []


@pytest.mark.parametrize("data", [b"[2026-09-14 08:01:22] run started\n", b"[docs](https://example.com)\n",
                                  b"{placeholder} text\n"])
def test_markdown_may_open_with_a_bracket(data):
    assert validate_artifact(data, "markdown").valid


@pytest.mark.parametrize("data", [b"[1, 2]", b'{"a": 1}'])
def test_real_json_is_still_not_markdown(data):
    assert not validate_artifact(data, "markdown").valid


def test_unreadable_standard_file_is_inaccessible_without_bedrock():
    provider = FakeProvider()
    decisions = decide([SourceFile("sbom.md", read_error="Permission denied")], provider)
    assert decisions["SBOM"].status == "INACCESSIBLE"
    assert decisions["SBOM"].detail == "Could not read sbom.md: Permission denied"
    assert provider.documents == []


def test_no_heading_and_bedrock_agrees():
    decisions, provider = run(without("sbom.md"), **{"sbom.md": b"packages\n"})
    sbom = decisions["SBOM"]
    assert (sbom.status, sbom.classified_by, sbom.evidence) == ("PRESENT", "bedrock", "packages")
    assert len(provider.documents) == 1


def test_no_heading_and_bedrock_names_another_category():
    decisions, provider = run(without("sbom.md", "user_instructions.md"), **{"sbom.md": b"guidance\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert "User Instructions" in decisions["SBOM"].detail
    # A wrong file never moves to the category it really belongs to
    assert decisions["User Instructions"].status == "MISSING"
    assert len(provider.documents) == 1


def test_wrong_heading_goes_to_bedrock():
    decisions, provider = run(without("sbom.md"), **{"sbom.md": b"# User Instructions\n\nguidance\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert len(provider.documents) == 1


def test_no_heading_and_bedrock_is_ambiguous():
    decisions, _ = run(without("sbom.md"), **{"sbom.md": b"packages and guidance\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert "could not be clearly identified" in decisions["SBOM"].detail


@pytest.mark.parametrize("failure, code", [(TimeoutError(), "timeout"), (RuntimeError("private"), "provider_error")])
def test_failed_bedrock_never_gives_present(failure, code):
    decisions, _ = run(without("sbom.md"), FakeProvider(failure=failure), **{"sbom.md": b"packages\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert code in decisions["SBOM"].detail
    assert "private" not in decisions["SBOM"].detail


def test_answer_with_made_up_evidence_counts_as_a_failure():
    provider = FakeProvider(evidence="words not in the document")
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert "malformed_response" in decisions["SBOM"].detail


def test_evidence_may_differ_from_the_document_only_in_whitespace():
    data = b"packages list:\n  openssl 3.0\r\n\tzlib 1.3\n"
    provider = FakeProvider(evidence="packages list: openssl 3.0 zlib 1.3")
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": data})
    assert decisions["SBOM"].status == "PRESENT"


@pytest.mark.parametrize("evidence", ["packages listing", "packageslist", "list: packages"])
def test_evidence_with_different_words_is_still_rejected(evidence):
    provider = FakeProvider(evidence=evidence)
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": b"packages list:\n"})
    assert decisions["SBOM"].status == "INVALID"


def test_switched_off_gives_invalid_with_a_reason():
    decisions = decide([SourceFile("sbom.md", data=b"packages\n")], None)
    assert decisions["SBOM"].status == "INVALID"
    assert "switched off" in decisions["SBOM"].detail


def test_non_ascii_evidence_is_accepted():
    data = "packages cost €15\n".encode()
    provider = FakeProvider(evidence="cost €15")
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": data})
    assert decisions["SBOM"].status == "PRESENT"


# Non-standard files


def test_renamed_files_fill_missing_categories():
    decisions, provider = run({
        "inventory.md": b"packages\n", "review.md": b"threats\n",
        "run.html": b"<p>results</p>", "start.md": b"guidance\n",
    })
    assert [decisions[category].source_file for category in CATEGORIES] == [
        "inventory.md", "review.md", "run.html", "start.md"]
    assert {decision.status for decision in decisions.values()} == {"PRESENT"}
    assert len(provider.documents) == 4


def test_case_changed_name_is_non_standard():
    decisions, provider = run({"SBOM.md": b"# SBOM\n\npackages\n"})
    assert (decisions["SBOM"].status, decisions["SBOM"].classified_by) == ("PRESENT", "bedrock")
    assert len(provider.documents) == 1


def test_renamed_cyclonedx_fills_sbom_without_bedrock():
    decisions, provider = run(without("sbom.md"), **{"parts.json": CYCLONEDX})
    assert (decisions["SBOM"].status, decisions["SBOM"].classified_by) == ("PRESENT", "rules")
    assert provider.documents == []


def test_standard_file_beats_non_standard_even_when_invalid():
    decisions, provider = run(without("sbom.md"), **{"sbom.md": b"", "inventory.md": b"packages\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert decisions["SBOM"].source_file == "sbom.md"
    assert provider.documents == []


def test_nothing_missing_means_no_calls_for_non_standard_files():
    _, provider = run(NORMAL, **{"README.md": b"# readme\n", "extra.md": b"packages\n"})
    assert provider.documents == []


def test_file_naming_a_filled_category_changes_nothing():
    decisions, provider = run(without("test_logs.md"), **{"extra.md": b"packages\n"})
    assert decisions["SBOM"].source_file == "sbom.md"
    assert decisions["Test Logs"].status == "MISSING"
    assert len(provider.documents) == 1


@pytest.mark.parametrize("data", [b"packages and threats\n", b"no known words\n"])
def test_unclear_non_standard_file_leaves_the_category_missing(data):
    decisions, _ = run(without("sbom.md"), **{"extra.md": data})
    assert decisions["SBOM"].status == "MISSING"


def test_failed_non_standard_file_leaves_the_category_missing():
    decisions, _ = run(without("sbom.md"), FakeProvider(failure=TimeoutError()), **{"extra.md": b"packages\n"})
    assert decisions["SBOM"].status == "MISSING"


@pytest.mark.parametrize("name, data", [("notes.docx", b"packages"), ("bad.json", b"{}"), ("empty.md", b"")])
def test_unusable_non_standard_file_is_ignored_without_bedrock(name, data, caplog):
    with caplog.at_level(logging.WARNING):
        decisions, provider = run(without("sbom.md"), **{name: data})
    assert decisions["SBOM"].status == "MISSING"
    assert provider.documents == []
    assert name in caplog.text


def test_switched_off_ignores_non_standard_files():
    decisions = decide([SourceFile("inventory.md", data=b"packages\n")], None)
    assert decisions["SBOM"].status == "MISSING"


# Several files for one category


def test_supported_file_beats_unsupported_one():
    decisions, _ = run(without("sbom.md"), **{"sbom.md": NORMAL["sbom.md"], "sbom.pdf": b"%PDF"})
    assert (decisions["SBOM"].status, decisions["SBOM"].source_file) == ("PRESENT", "sbom.md")


def test_two_supported_standard_files_are_invalid_naming_both():
    decisions, provider = run(without("sbom.md"), **{"sbom.md": NORMAL["sbom.md"], "sbom.json": CYCLONEDX})
    assert decisions["SBOM"].status == "INVALID"
    assert "sbom.json" in decisions["SBOM"].detail and "sbom.md" in decisions["SBOM"].detail
    assert provider.documents == []


def test_two_non_standard_files_are_invalid_naming_both():
    decisions, _ = run(without("sbom.md"), **{"a.md": b"packages one\n", "b.md": b"packages two\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert "a.md" in decisions["SBOM"].detail and "b.md" in decisions["SBOM"].detail


# Limits and what the provider receives


def test_only_the_first_ten_non_standard_files_are_classified():
    files = {f"file{index:02}.md": f"unclear {index}\n".encode() for index in range(11)}
    files["zzz.md"] = b"packages\n"
    decisions, provider = run(without("sbom.md"), **files)
    assert len(provider.documents) == 10
    assert decisions["SBOM"].status == "MISSING"


def test_cyclonedx_does_not_use_up_the_limit():
    files = {f"parts{index}.json": CYCLONEDX for index in range(3)}
    decisions, provider = run(without("sbom.md"), **files)
    assert provider.documents == []
    assert decisions["SBOM"].status == "INVALID"


def test_size_limit_is_inclusive():
    limit = artifact_pipeline.MAX_CLASSIFIED_BYTES
    at_limit = b"packages\n".ljust(limit, b" ")
    decisions, provider = run(without("sbom.md"), **{"sbom.md": at_limit})
    assert decisions["SBOM"].status == "PRESENT"
    assert len(provider.documents) == 1

    artifact_pipeline.clear_proposal_cache()
    decisions, provider = run(without("sbom.md"), **{"sbom.md": at_limit + b" "})
    assert decisions["SBOM"].status == "INVALID"
    assert provider.documents == []

    decisions, provider = run(without("sbom.md"), **{"extra.md": at_limit + b" "})
    assert decisions["SBOM"].status == "MISSING"
    assert provider.documents == []


def test_provider_gets_the_whole_document_and_no_name_or_category():
    body = "packages\n" + "x" * 500_000 + "\nthe end\n"
    _, provider = run(without("sbom.md"), **{"secret-name.md": body.encode()})
    system_prompt, artifact_json = provider.documents[0]
    assert json.loads(artifact_json) == {"document": body}
    assert "secret-name" not in system_prompt + artifact_json


# Cache


def test_same_content_is_classified_once_even_under_two_names():
    provider = FakeProvider()
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    run(without("sbom.md"), provider, **{"other.md": b"packages\n"})
    assert len(provider.documents) == 1


def test_new_content_prompt_or_model_is_classified_again(monkeypatch):
    provider = FakeProvider()
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    run(without("sbom.md"), provider, **{"sbom.md": b"packages v2\n"})
    assert len(provider.documents) == 2
    monkeypatch.setattr(artifact_pipeline, "PROMPT_VERSION", "new-prompt")
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert len(provider.documents) == 3
    provider.model_id = "new-model"
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert len(provider.documents) == 4


def test_one_malformed_reply_is_retried():
    class OneBadReply(FakeProvider):
        def classify(self, **kwargs):
            answer = super().classify(**kwargs)
            return "not json" if len(self.documents) == 1 else answer

    provider = OneBadReply()
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert decisions["SBOM"].status == "PRESENT"
    assert len(provider.documents) == 2


def test_repeated_malformed_replies_stop_after_one_retry():
    class AlwaysBad(FakeProvider):
        def classify(self, **kwargs):
            super().classify(**kwargs)
            return "not json"

    provider = AlwaysBad()
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert decisions["SBOM"].status == "INVALID"
    assert len(provider.documents) == 2


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError()])
def test_provider_failures_are_not_retried(failure):
    provider = FakeProvider(failure=failure)
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert len(provider.documents) == 1


def test_failures_are_not_cached():
    provider = FakeProvider(failure=TimeoutError())
    run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    provider.failure = None
    decisions, _ = run(without("sbom.md"), provider, **{"sbom.md": b"packages\n"})
    assert len(provider.documents) == 2
    assert decisions["SBOM"].status == "PRESENT"


# Switch


@pytest.mark.parametrize("value", [None, "", "0", "true"])
def test_provider_stays_off_unless_exactly_enabled(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("CRA_BEDROCK_ENABLED", value)
    assert artifact_pipeline.get_provider() is None


def test_enabled_without_configuration_gives_no_provider(monkeypatch):
    monkeypatch.setenv("CRA_BEDROCK_ENABLED", "1")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    artifact_pipeline._bedrock_provider.cache_clear()
    assert artifact_pipeline.get_provider() is None


# Full pipeline through the API, on the committed bedrock-test releases


@pytest.fixture
def client():
    bundles.clear()
    bundle_service.archives.clear()
    return app.test_client()


@pytest.fixture
def provider(monkeypatch):
    # The fixture documents use real wording, so the fake recognizes them by a distinctive phrase
    fake = FakeProvider()
    monkeypatch.setitem(ANSWERS, "openssl", "SBOM")
    monkeypatch.setitem(ANSWERS, "Threat:", "Risk Assessment")
    monkeypatch.setitem(ANSWERS, "test_login", "Test Logs")
    monkeypatch.setitem(ANSWERS, "To install", "User Instructions")
    monkeypatch.setattr(artifact_pipeline, "get_provider", lambda: fake)
    return fake


def create(client, release, product="bedrock-test"):
    response = client.post("/bundles", json={"product_id": product, "release_version": release})
    assert response.status_code == 202
    return client.get(f"/bundles/{response.get_json()['bundle_id']}").get_json()


def statuses(body):
    return {item["category"]: item["status"] for item in body["manifest"]}


def archive_names(client, body):
    response = client.get(body["download_url"])
    return set(zipfile.ZipFile(io.BytesIO(response.data)).namelist())


@pytest.mark.parametrize("release, calls, expected_files", [
    ("v1.0-no-headings", 4, {"sbom.md", "risk_assessment.md", "test_logs.md", "user_instructions.md"}),
    ("v1.1-renamed", 4, {"component_inventory.md", "threat_review.md", "qa_run.md", "getting_started.md"}),
    ("v1.4-formats", 0, {"sbom.json", "risk_assessment.html", "test_logs.html", "user_instructions.html"}),
    ("v1.6-injection", 1, {"sbom.md", "risk_assessment.md", "test_logs.md", "user_instructions.md"}),
])
def test_complete_test_releases(client, provider, release, calls, expected_files):
    body = create(client, release)
    assert body["status"] == "COMPLETED", body["manifest"]
    assert len(provider.documents) == calls
    assert archive_names(client, body) == expected_files | {"manifest.json"}


@pytest.mark.parametrize("release, calls, removed", [
    ("v1.2-wrong-content", 1, "sbom.md"), ("v1.3-unsupported", 0, "sbom.pdf"), ("v1.5-duplicate", 2, "components_a.md"),
])
def test_test_releases_with_a_bad_sbom(client, provider, release, calls, removed):
    body = create(client, release)
    assert body["status"] == "PARTIAL_SUCCESS"
    assert statuses(body) == {"SBOM": "INVALID", "Risk Assessment": "PRESENT", "Test Logs": "PRESENT",
                              "User Instructions": "PRESENT"}
    assert len(provider.documents) == calls
    assert removed not in archive_names(client, body)


def test_duplicate_release_names_both_files(client, provider):
    sbom = create(client, "v1.5-duplicate")["manifest"][0]
    assert "components_a.md" in sbom["source_failure_detail"]
    assert "components_b.md" in sbom["source_failure_detail"]


def test_injection_document_is_only_ever_sent_as_data(client, provider):
    create(client, "v1.6-injection")
    system_prompt, artifact_json = provider.documents[0]
    assert "Ignore your instructions" not in system_prompt
    assert "Ignore your instructions" in json.loads(artifact_json)["document"]


def test_manifest_records_how_each_category_was_settled(client, provider):
    body = create(client, "v1.1-renamed")
    for item in body["manifest"]:
        assert item["classified_by"] == "bedrock"
        assert item["source_file"]
        assert item["classification_evidence"]
    rules_only = create(client, "v1.4-formats")
    for item in rules_only["manifest"]:
        assert item["classified_by"] == "rules"
        assert "classification_evidence" not in item


@pytest.mark.parametrize("release", ["v1.0-no-headings", "v1.1-renamed", "v1.4-formats"])
def test_zip_holds_each_file_byte_for_byte(client, provider, release):
    body = create(client, release)
    archive = zipfile.ZipFile(io.BytesIO(client.get(body["download_url"]).data))
    folder = ROOT_FIXTURES / "bedrock-test" / release
    assert {name: archive.read(name) for name in archive.namelist() if name != "manifest.json"} == {
        path.name: path.read_bytes() for path in folder.iterdir()}


@pytest.mark.parametrize("data", [b"\xef\xbb\xbf# SBOM\r\n", "# SBOM\n€15  \n\n".encode()])
def test_zip_keeps_bom_line_endings_and_non_ascii(client, provider, tmp_path, monkeypatch, data):
    folder = tmp_path / "p" / "v1"
    folder.mkdir(parents=True)
    (folder / "sbom.md").write_bytes(data)
    monkeypatch.setattr(artifact_pipeline, "FIXTURES_ROOT", tmp_path)
    body = create(client, "v1", product="p")
    archive = zipfile.ZipFile(io.BytesIO(client.get(body["download_url"]).data))
    assert archive.read("sbom.md") == data


def test_repeat_runs_give_identical_zips_with_bedrock(client, provider):
    first, second = create(client, "v1.1-renamed"), create(client, "v1.1-renamed")
    assert len(provider.documents) == 4
    assert client.get(first["download_url"]).data == client.get(second["download_url"]).data


def test_bedrock_off_on_a_test_release_never_gives_present(client):
    body = create(client, "v1.0-no-headings")
    assert body["status"] == "PARTIAL_SUCCESS"
    assert set(statuses(body).values()) == {"INVALID"}
    assert "download_url" in body


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("no permission")])
def test_broken_bedrock_on_a_test_release_never_gives_present(client, monkeypatch, failure):
    monkeypatch.setattr(artifact_pipeline, "get_provider", lambda: FakeProvider(failure=failure))
    body = create(client, "v1.0-no-headings")
    assert set(statuses(body).values()) == {"INVALID"}


@pytest.mark.parametrize("release", ["v1.2", "v1.3", "v1.4"])
def test_shared_fixtures_never_call_bedrock(client, provider, release):
    create(client, release, product="prd-88")
    assert provider.documents == []


# Background processing


@pytest.fixture
def background(monkeypatch, client):
    monkeypatch.setitem(app.config, "PROCESS_BUNDLES_INLINE", False)
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(app_module, "executor", pool)
    yield pool
    pool.shutdown(wait=True)


def test_post_returns_before_classification_finishes(client, background, monkeypatch):
    started, release = Event(), Event()

    class WaitingProvider(FakeProvider):
        def classify(self, **kwargs):
            started.set()
            assert release.wait(5)
            return super().classify(**kwargs)

    monkeypatch.setitem(ANSWERS, "openssl", "SBOM")
    monkeypatch.setattr(artifact_pipeline, "get_provider", WaitingProvider)
    response = client.post("/bundles", json={"product_id": "bedrock-test", "release_version": "v1.5-duplicate"})
    assert response.status_code == 202
    assert response.get_json()["status"] == "PENDING"
    bundle_id = response.get_json()["bundle_id"]

    assert started.wait(5)
    during = client.get(f"/bundles/{bundle_id}")
    assert during.get_json()["status"] == "PROCESSING"
    assert client.get(f"/bundles/{bundle_id}/download").status_code == 404

    release.set()
    background.shutdown(wait=True)
    assert client.get(f"/bundles/{bundle_id}").get_json()["status"] == "PARTIAL_SUCCESS"
    assert client.get(f"/bundles/{bundle_id}/download").status_code == 200


def test_unexpected_failure_gives_failed_and_never_stays_stuck(client, background, monkeypatch):
    def explode(bundle):
        raise RuntimeError("private detail")

    monkeypatch.setattr(bundle_service, "retrieve_and_classify", explode)
    response = client.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"})
    bundle_id = response.get_json()["bundle_id"]
    background.shutdown(wait=True)
    body = client.get(f"/bundles/{bundle_id}").get_json()
    assert body["status"] == "FAILED"
    assert "private detail" not in json.dumps(body)


def test_bundles_running_together_do_not_mix(client, background):
    ids = {}
    for release in ("v1.2", "v1.3", "v1.4"):
        response = client.post("/bundles", json={"product_id": "prd-88", "release_version": release})
        ids[release] = response.get_json()["bundle_id"]
    background.shutdown(wait=True)
    assert statuses(client.get(f"/bundles/{ids['v1.2']}").get_json())["Test Logs"] == "PRESENT"
    assert statuses(client.get(f"/bundles/{ids['v1.3']}").get_json())["Test Logs"] == "MISSING"
    assert statuses(client.get(f"/bundles/{ids['v1.4']}").get_json())["SBOM"] == "INVALID"
