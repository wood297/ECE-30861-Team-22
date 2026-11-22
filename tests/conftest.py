import os
import sys
from pathlib import Path

import pytest

# Make app.py, artifact_pipeline.py and the test_runner package importable from tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import artifact_pipeline  # noqa: E402
from account_config import ACCOUNT_PATH, load_account  # noqa: E402
from connectors.base import FetchResult  # noqa: E402
from github_client import ConfirmedArtifactMissing  # noqa: E402

# Committed copies of every file test_account.json pins in an external source, laid out as
# tests/fixtures/<connector type>/<path or filename>
OFFLINE_SOURCES = Path(__file__).parent / "fixtures"
REAL_RUN_CONNECTOR = artifact_pipeline.run_connector


def offline_run_connector(connector, conn, ref, **options):
    """Stands in for Jenkins, GitHub and Confluence: a pinned file is retrieved when its committed
    copy exists and NOT_FOUND otherwise (e.g. the -missing release). Local fixtures, and tests that
    set CONNECTOR_HTTP_OPTIONS to a fake network, run the real connector."""
    if connector.type == "fixtures" or artifact_pipeline.CONNECTOR_HTTP_OPTIONS is not None:
        return REAL_RUN_CONNECTOR(connector, conn, ref, **options)
    for name in ref.get("paths") or [ref.get("filename")]:
        path = OFFLINE_SOURCES / connector.type / str(name)
        if path.is_file():
            return FetchResult.retrieved(path.read_bytes(), path.name, version="offline")
    return FetchResult.not_found(f"{connector.display_name} has none of the pinned files (offline copy).")


class AbsentAtCommitClient:
    """Stands in for GitHub: every file is absent at the pinned commit, which is exactly what the
    real catalog entry for prd-88 v1.2-github-inaccessible sees. Tests never touch the network."""

    def get_file(self, path, ref):
        raise ConfirmedArtifactMissing("absent")


@pytest.fixture(autouse=True, scope="session")
def process_bundles_inline():
    """Bundles finish within the POST, including for the session-wide acceptance report."""
    from app import app

    app.config["PROCESS_BUNDLES_INLINE"] = True


# Session scope, so it is already in place when module-scoped fixtures (test_acceptance's
# report) run the suite
@pytest.fixture(autouse=True, scope="session")
def offline_github():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(artifact_pipeline, "make_github_client", lambda repository: AbsentAtCommitClient())
        artifact_pipeline.clear_github_cache()
        yield
        artifact_pipeline.clear_github_cache()


@pytest.fixture(autouse=True, scope="session")
def offline_connectors():
    """The committed test_account.json, whatever CRA_TEST_ACCOUNT says, with no network."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(artifact_pipeline, "ACCOUNT", load_account(ACCOUNT_PATH))
        patch.setattr(artifact_pipeline, "run_connector", offline_run_connector)
        yield


@pytest.fixture(autouse=True, scope="session")
def no_rate_limits():
    """One test process sends thousands of requests from 127.0.0.1; tests that check the limits
    turn them back on (rate_limits.set_enabled)."""
    import rate_limits
    rate_limits.set_enabled(False)
    yield


@pytest.fixture(autouse=True)
def isolate_connectors(monkeypatch):
    """No test reads a developer's real tokens or another test's cached fetches, from the
    connectors or from the Phase 4 GitHub catalog (a fake GitHub that served a file must not
    leave it cached for v1.2-github-inaccessible)."""
    monkeypatch.delenv(artifact_pipeline.LOCAL_SECRETS_ENV, raising=False)
    monkeypatch.delenv(artifact_pipeline.SECRET_PREFIX_ENV, raising=False)
    artifact_pipeline.clear_pinned_cache()
    artifact_pipeline.clear_github_cache()


@pytest.fixture(autouse=True)
def isolate_classification(monkeypatch):
    """No test can reach real Bedrock unless it opts in, or reuse another test's answers."""
    monkeypatch.delenv("CRA_BEDROCK_ENABLED", raising=False)
    artifact_pipeline.clear_proposal_cache()
