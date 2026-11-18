"""Confluence behavior plus the shared HTTP connector acceptance contract."""
import pytest

from connector_contract import FakeResponse, FakeSource, HttpConnectorContract
from connectors.base import NOT_FOUND, RETRIEVED, SOURCE_ERROR, SettingsError
from connectors.confluence import ConfluenceConnector

DOCUMENT = b"# Risk Assessment\r\nSynthetic fixture.\r\n"


class TestConfluenceContract(HttpConnectorContract):
    connector = ConfluenceConnector()
    settings = {"cloud_id": "43c0f648-dc39-4501-a7ca-bc8bf841ad48", "email": "tester@example.com"}
    ref = {"page_id": "622599", "filename": "risk_assessment.md", "version": 1}
    expected_filename = "risk_assessment.md"
    expected_bytes = DOCUMENT

    def serve_file(self, request):
        if "/download?" in request.target:
            assert request.target.endswith("version=1")
            return FakeResponse(200, DOCUMENT)
        return FakeResponse.json({"results": [{"id": "att123", "title": "risk_assessment.md"}]})

    def serve_missing(self, request):
        return FakeResponse.json({"results": []})

    def test_pagination(self):
        def handler(request):
            if "cursor=" in request.target or "/download?" in request.target:
                return self.serve_file(request)
            return FakeResponse.json({"results": [], "_links": {
                "next": "/wiki/api/v2/pages/622599/attachments?cursor=next"}})
        assert self.fetch(FakeSource(handler)).outcome == RETRIEVED

    def test_duplicate_names(self):
        source = FakeSource(lambda request: FakeResponse.json({"results": [
            {"id": "att123", "title": "risk_assessment.md"},
            {"id": "att124", "title": "risk_assessment.md"}]}))
        assert self.fetch(source).outcome == SOURCE_ERROR

    def test_exact_case(self):
        source = FakeSource(lambda request: FakeResponse.json({"results": [
            {"id": "att123", "title": "Risk_Assessment.md"}]}))
        assert self.fetch(source).outcome == NOT_FOUND

    def test_redirect_has_no_authorization(self):
        def handler(request):
            if request.host == "api.media.atlassian.com":
                assert "Authorization" not in request.headers
                return FakeResponse(200, DOCUMENT)
            if "/download?" in request.target:
                return FakeResponse.redirect("https://api.media.atlassian.com/file")
            return self.serve_file(request)
        result = self.fetch(FakeSource(handler))
        assert (result.data, result.version) == (DOCUMENT, "1")

    def test_looping_pagination(self):
        source = FakeSource(lambda request: FakeResponse.json({"results": [], "_links": {
            "next": "/wiki/api/v2/pages/622599/attachments"}}))
        assert self.fetch(source).outcome == SOURCE_ERROR

    def test_absent_pinned_version(self):
        def handler(request):
            if "/download?" in request.target:
                return FakeResponse(404)
            return self.serve_file(request)
        assert self.fetch(FakeSource(handler)).outcome == NOT_FOUND

    def test_pagination_cannot_change_hosts(self):
        source = FakeSource(lambda request: FakeResponse.json({"results": [], "_links": {
            "next": "https://other.example/attachments"}}))
        assert self.fetch(source).outcome == SOURCE_ERROR
        assert len(source.requests) == 1

    @pytest.mark.parametrize("version", [0, -1])
    def test_requires_positive_version(self, version):
        with pytest.raises(SettingsError):
            self.connector.validate_ref({**self.ref, "version": version})
