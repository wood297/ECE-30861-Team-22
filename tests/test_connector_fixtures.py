"""The contract tests on the fixtures connector."""
import pytest

from connector_contract import FileConnectorContract
from connectors.base import RETRIEVED, SettingsError
from connectors.fixtures import FIXTURES_ROOT, FixturesConnector


class TestFixturesContract(FileConnectorContract):
    connector = FixturesConnector()
    ref = {"paths": ["{product}/{release}/sbom.md"]}
    missing_ref = {"paths": ["{product}/{release}/no-such-file.md"]}
    release = "v1.2"
    expected_filename = "sbom.md"
    expected_bytes = (FIXTURES_ROOT / "prd-88" / "v1.2" / "sbom.md").read_bytes()

    @pytest.mark.parametrize("paths", [["../secret_store.py"], ["/etc/passwd"], ["a/../../b"], ["{org}/x.md"]])
    def test_paths_outside_the_fixtures_folder_are_refused(self, paths):
        with pytest.raises(SettingsError):
            self.connector.validate_ref({"paths": paths})

    def test_a_filled_in_release_cant_escape_the_folder(self):
        self.release = "../.."
        result = self.fetch()
        assert result.outcome != RETRIEVED

    def test_the_first_existing_candidate_wins(self):
        self.ref = {"paths": ["{product}/{release}/sbom.json", "{product}/{release}/sbom.md"]}
        assert self.fetch().filename == "sbom.md"

    def test_files_over_the_size_limit_are_refused(self):
        self.connector = FixturesConnector(max_bytes=10)
        assert self.fetch().outcome != RETRIEVED
