import os
import sys

import pytest

# Make app.py, artifact_pipeline.py and the test_runner package importable from tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import artifact_pipeline  # noqa: E402
from github_client import ConfirmedArtifactMissing  # noqa: E402


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


@pytest.fixture(autouse=True)
def isolate_classification(monkeypatch):
    """No test can reach real Bedrock unless it opts in, or reuse another test's answers."""
    monkeypatch.delenv("CRA_BEDROCK_ENABLED", raising=False)
    artifact_pipeline.clear_proposal_cache()
