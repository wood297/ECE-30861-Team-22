import os
import sys

# Make app.py, artifact_pipeline.py and the test_runner package importable from tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
