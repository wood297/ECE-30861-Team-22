"""The Jenkins connector: the shared contract tests, plus what is specific to Jenkins."""
import base64
import json

import pytest
from connector_contract import SECRET, FakeRequest, FakeResponse, HttpConnectorContract

from connectors.base import AUTH_FAILED, NOT_FOUND, RETRIEVED, SOURCE_ERROR, UNAVAILABLE, SettingsError
from connectors.jenkins import JenkinsConnector

HOST = "jenkins.example.com"
LOG_BYTES = b"# Test Logs: prd-88 v5.0\r\n\r\nAll passed.\r\n"
PATH = "artifacts/prd-88/v5.0/test_logs.md"
BUILD = "/job/pod/job/cra-test-logs/2"


def listing(*paths: str, building: bool = False, result: str = "SUCCESS") -> FakeResponse:
    return FakeResponse.json({"_class": "org.jenkinsci.plugins.workflow.job.WorkflowRun", "building": building,
                              "result": result, "artifacts": [{"relativePath": p} for p in paths]})


def jenkins(listing_response: FakeResponse, artifact: FakeResponse | None = None):
    """A Jenkins that answers the build listing and the artifact download."""
    def handler(request: FakeRequest) -> FakeResponse:
        if request.target.startswith(f"{BUILD}/api/json?"):
            return listing_response
        if request.target == f"{BUILD}/artifact/{PATH}":
            return artifact or FakeResponse(200, LOG_BYTES, {"Content-Type": "application/octet-stream"})
        return FakeResponse(404, b"<html>Not Found</html>")
    return handler


class TestJenkinsContract(HttpConnectorContract):
    connector = JenkinsConnector()
    settings = {"url": f"https://{HOST}", "user": "team22"}
    ref = {"job": "pod/cra-test-logs", "build": 2, "paths": ["artifacts/{product}/{release}/test_logs.md"]}
    expected_filename = "test_logs.md"
    expected_bytes = LOG_BYTES

    def serve_file(self, request):
        return jenkins(listing(PATH))(request)

    def serve_missing(self, request):
        return jenkins(listing())(request)


class TestJenkins:
    """Jenkins-specific behaviour, through the same fake network and safe_http as the contract."""

    def run(self, handler, settings=None, **ref_changes):
        harness = TestJenkinsContract()
        harness.ref = {**harness.ref, **ref_changes}
        if settings is not None:
            harness.settings = settings
        source = harness.source(handler)
        return harness.fetch(source), source

    def test_requests_use_folder_paths_basic_auth_and_no_secret_in_urls(self):
        result, source = self.run(jenkins(listing(PATH)))
        assert result.outcome == RETRIEVED
        listing_request, artifact_request = source.requests
        assert listing_request.target == f"{BUILD}/api/json?tree=building,result,artifacts%5BrelativePath%5D"
        assert artifact_request.target == f"{BUILD}/artifact/{PATH}"
        expected = "Basic " + base64.b64encode(f"team22:{SECRET}".encode()).decode()
        assert all(r.headers["Authorization"] == expected for r in source.requests)
        assert all(r.headers["User-Agent"] == "cra-documentation-service" for r in source.requests)
        assert all(r.method == "GET" for r in source.requests)

    def test_version_names_the_job_and_build(self):
        result, _ = self.run(jenkins(listing(PATH)))
        assert result.version == "pod/cra-test-logs#2"

    def test_a_url_path_prefix_is_kept(self):
        seen = []
        result, _ = self.run(lambda request: seen.append(request.target) or FakeResponse(404),
                             settings={"url": f"https://{HOST}/jenkins/", "user": "team22"})
        assert result.outcome == NOT_FOUND
        assert seen[0].startswith("/jenkins/job/pod/job/cra-test-logs/2/api/json")

    def test_a_failed_build_that_archived_the_file_is_still_retrieved(self):
        # We collect evidence; whether the tests passed is for the reader of the report
        result, _ = self.run(jenkins(listing(PATH, result="FAILURE")))
        assert result.outcome == RETRIEVED and result.data == LOG_BYTES

    def test_a_running_build_is_unavailable(self):
        result, source = self.run(jenkins(listing(PATH, building=True)))
        assert result.outcome == UNAVAILABLE and "still running" in (result.detail or "")
        assert len(source.requests) == 1

    def test_an_unknown_build_is_not_found(self):
        result, _ = self.run(lambda request: FakeResponse(404, b"<html>Not Found</html>"), build=99)
        assert result.outcome == NOT_FOUND and "no build 99" in (result.detail or "")

    def test_a_build_without_the_artifact_is_not_found_without_a_download(self):
        result, source = self.run(jenkins(listing("artifacts/other/file.md")))
        assert result.outcome == NOT_FOUND
        assert len(source.requests) == 1

    def test_an_artifact_deleted_after_the_listing_is_not_found(self):
        result, _ = self.run(jenkins(listing(PATH), FakeResponse(404, b"gone")))
        assert result.outcome == NOT_FOUND

    def test_the_first_archived_candidate_wins(self):
        other = "artifacts/prd-88/v5.0/test_logs.html"
        seen = []

        def handler(request):
            seen.append(request.target)
            if "/api/json" in request.target:
                return listing(other, PATH)
            return FakeResponse(200, b"<h1>Test Logs</h1>")
        result, _ = self.run(handler, paths=["artifacts/{product}/{release}/missing.md", other, PATH])
        assert result.outcome == RETRIEVED and result.filename == "test_logs.html"
        assert seen[-1] == f"{BUILD}/artifact/{other}"

    def test_an_empty_artifact_is_retrieved_as_empty_bytes(self):
        # The pipeline, not the connector, decides an empty file is INVALID
        result, _ = self.run(jenkins(listing(PATH), FakeResponse(200, b"")))
        assert result.outcome == RETRIEVED and result.data == b""

    def test_a_cloudflare_block_says_so(self):
        page = FakeResponse(403, b"<html>Attention Required! | Cloudflare</html>", {"Server": "cloudflare"})
        result, _ = self.run(lambda request: page)
        assert result.outcome == AUTH_FAILED and "Cloudflare" in (result.detail or "")

    def test_a_jenkins_permission_error_is_a_normal_auth_failure(self):
        page = FakeResponse(403, b"<html>Access Denied</html>", {"Server": "cloudflare", "X-Jenkins": "2.580.2"})
        result, _ = self.run(lambda request: page)
        assert result.outcome == AUTH_FAILED and "Cloudflare" not in (result.detail or "")

    @pytest.mark.parametrize("body", [
        {"building": False},
        {"building": "no", "artifacts": []},
        {"building": False, "artifacts": [{"relativePath": 7}]},
        {"building": False, "artifacts": ["artifacts/x.md"]},
    ])
    def test_a_malformed_listing_is_a_source_error(self, body):
        result, _ = self.run(lambda request: FakeResponse(200, json.dumps(body).encode()))
        assert result.outcome == SOURCE_ERROR

    def test_the_encoded_credentials_never_reach_logs_or_details(self, caplog):
        caplog.set_level("DEBUG")
        result, _ = self.run(jenkins(listing()))
        encoded = base64.b64encode(f"team22:{SECRET}".encode()).decode()
        assert encoded not in caplog.text and encoded not in (result.detail or "")


class TestJenkinsValidation:
    connector = JenkinsConnector()

    @pytest.mark.parametrize("url", ["http://jenkins.example.com", "https://jenkins.example.com:8080",
                                     "https://user@jenkins.example.com", "https://jenkins.example.com/?a=1",
                                     "https://jenkins.example.com/#top", "https://"])
    def test_bad_addresses_are_refused(self, url):
        with pytest.raises(SettingsError):
            self.connector.validate_settings({"url": url, "user": "team22"})

    def test_user_names_cannot_contain_a_colon(self):
        with pytest.raises(SettingsError):
            self.connector.validate_settings({"url": "https://jenkins.example.com", "user": "team:22"})

    def test_the_address_is_normalized(self):
        clean = self.connector.validate_settings({"url": "https://jenkins.example.com/ci/", "user": "team22"})
        assert clean["url"] == "https://jenkins.example.com/ci"
        assert self.connector.allowed_hosts(clean) == ["jenkins.example.com"]

    @pytest.mark.parametrize("build", [0, -1, "lastSuccessfulBuild", "{release}", True, 10**10])
    def test_only_fixed_build_numbers_are_accepted(self, build):
        with pytest.raises(SettingsError):
            self.connector.validate_ref({"job": "pod/cra-test-logs", "build": build, "paths": ["a.md"]})

    def test_a_build_number_may_be_given_as_text(self):
        clean = self.connector.validate_ref({"job": "pod/cra-test-logs", "build": "4", "paths": ["a.md"]})
        assert clean["build"] == 4

    @pytest.mark.parametrize("ref", [
        {"job": "pod/../admin", "build": 2, "paths": ["a.md"]},
        {"job": "/pod", "build": 2, "paths": ["a.md"]},
        {"job": "a/b/c/d/e/f", "build": 2, "paths": ["a.md"]},
        {"job": "pod/cra-test-logs", "build": 2, "paths": ["../secrets.md"]},
        {"job": "pod/cra-test-logs", "build": 2, "paths": ["a/..b.md"]},
        {"job": "pod/cra-test-logs", "build": 2, "paths": ["/etc/passwd"]},
    ])
    def test_unsafe_job_and_artifact_paths_are_refused(self, ref):
        with pytest.raises(SettingsError):
            self.connector.validate_ref(ref)
