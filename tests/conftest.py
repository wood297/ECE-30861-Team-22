import os
import sys

import pytest

# Make app.py, artifact_pipeline.py and the test_runner package importable from tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True, scope="session")
def process_bundles_inline():
    """Bundles finish within the POST, including for the session-wide acceptance report."""
    from app import app

    app.config["PROCESS_BUNDLES_INLINE"] = True


@pytest.fixture(autouse=True)
def isolate_classification(monkeypatch):
    """No test can reach real Bedrock unless it opts in, or reuse another test's answers."""
    from artifact_pipeline import clear_proposal_cache

    monkeypatch.delenv("CRA_BEDROCK_ENABLED", raising=False)
    clear_proposal_cache()
