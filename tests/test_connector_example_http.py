"""The contract tests on the example HTTP connector: the template for a new connector's tests."""
import pytest

from connector_contract import SECRET, FakeResponse, FakeSource, HttpConnectorContract
from connectors.base import NOT_FOUND, RETRIEVED, SettingsError
from connectors.example_http import ExampleHttpConnector

SBOM = b'{"bomFormat": "CycloneDX", "specVersion": "1.5"}'


class TestExampleHttpContract(HttpConnectorContract):
    connector = ExampleHttpConnector()
    settings = {"base_url": "https://files.example.com/docs/"}
    ref = {"paths": ["{product}/{release}/sbom.json", "{product}/{release}/sbom.xml"]}
    expected_filename = "sbom.json"
    expected_bytes = SBOM
    registered = False

    def serve_file(self, request):
        if request.headers.get("Authorization") != f"Bearer {SECRET}":
            return FakeResponse(401)
        if request.target == "/docs/prd-88/v5.0/sbom.json":
            return FakeResponse(200, SBOM, {"ETag": '"abc123"'})
        return FakeResponse(404)

    def serve_missing(self, request):
        return FakeResponse(404)

    # Connector-specific tests go next to the contract

    def test_tries_the_next_path_only_after_a_confirmed_absence(self):
        def only_xml(request):
            return FakeResponse(200, b"<bom/>") if request.target.endswith(".xml") else FakeResponse(404)
        source = FakeSource(only_xml)
        result = self.fetch(source)
        assert (result.outcome, result.filename) == (RETRIEVED, "sbom.xml")
        assert [r.target for r in source.requests] == ["/docs/prd-88/v5.0/sbom.json", "/docs/prd-88/v5.0/sbom.xml"]

        source = FakeSource(lambda request: FakeResponse(503))
        assert self.fetch(source).outcome != NOT_FOUND
        assert len(source.requests) == 1, "a failure must not fall through to the next candidate"

    def test_reports_the_version_it_got(self):
        assert self.fetch(self.source()).version == '"abc123"'

    @pytest.mark.parametrize("base_url", ["http://files.example.com", "https://files.example.com:8443",
                                          "https://files.example.com/?x=1", "https://u:p@files.example.com"])
    def test_refuses_unusual_server_addresses(self, base_url):
        with pytest.raises(SettingsError):
            self.connector.validate_settings({"base_url": base_url})
