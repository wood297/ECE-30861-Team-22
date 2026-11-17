"""The `fixtures` connector: files from this repository's fixtures/ folder (Phase 5 plan, §5, §7).

Only the pod-test organization may have a fixtures connection (enforced where connections are
created, step 2.1), so the class test suite's releases go through the same connector code as a
company's. It's also the smallest complete connector, for authors to read before example_http.py.

    settings: none
    ref:      paths = ["{product}/{release}/sbom.md"]    tried in order; the first that exists wins
"""
from pathlib import Path
from typing import Any, Mapping

from connectors.base import LIST, BaseConnector, FetchContext, FetchResult, Field, SettingsError
from safe_http import DEFAULT_MAX_BYTES
from tenant_store import Connection

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "fixtures"
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"


class FixturesConnector(BaseConnector):
    type = "fixtures"
    display_name = "fixtures"
    settings_fields = ()
    ref_fields = (
        Field("paths", "File paths, tried in order", kind=LIST, pattern=rf"{_SEGMENT}(/{_SEGMENT}){{0,3}}",
              help="Relative to fixtures/, e.g. {product}/{release}/sbom.md"),
    )

    def __init__(self, root: Path = FIXTURES_ROOT, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = root
        self.max_bytes = max_bytes

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_ref(ref)
        if any(".." in path for path in clean["paths"]):
            raise SettingsError("paths", "must not contain '..'")
        return clean

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        return []   # never uses the network

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        root = self.root.resolve()
        for relative in ref["paths"]:
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                return FetchResult.source_error("The fixture path leaves the fixtures folder.")
            if not path.is_file():
                continue   # a confirmed absence: try the next candidate
            if path.stat().st_size > self.max_bytes:
                return FetchResult.source_error("The fixture file is larger than the size limit.")
            try:
                data = path.read_bytes()
            except OSError as error:
                return FetchResult.unavailable(f"The fixture file could not be read ({error.strerror}).")
            return FetchResult.retrieved(data, path.name)
        return FetchResult.not_found("No fixture file at the pinned paths.")
