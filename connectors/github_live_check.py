"""Compare a pinned GitHub connector fetch with a trusted local copy."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from connectors import REGISTRY, make_context
from connectors.base import RETRIEVED, fill_ref, run_fetch
from connectors.try_fetch import BUNDLE_SECONDS, LOCAL_ORG
from secret_store import LocalFileSecretStore, SecretStoreError
from tenant_store import Connection, check_uuid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection", type=Path, help="GitHub connection JSON file")
    parser.add_argument("expected", type=Path, help="trusted local copy of the pinned file")
    parser.add_argument("--product", default="prd-88")
    parser.add_argument("--release", default="v5.0")
    args = parser.parse_args(argv)

    connector = REGISTRY["github"]
    try:
        spec = json.loads(args.connection.read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or spec.get("type") != "github" or not isinstance(spec.get("conn_id"), str):
            raise ValueError("invalid GitHub connection")
        conn_id = check_uuid(spec["conn_id"], "conn_id")
        settings = connector.validate_settings(spec["settings"])
        ref = fill_ref(connector.validate_ref(spec["ref"]), args.product, args.release)
        expected_digest = hashlib.sha256(args.expected.read_bytes()).hexdigest()
        secrets = LocalFileSecretStore(os.environ["CRA_LOCAL_SECRETS"])
        conn = Connection(LOCAL_ORG, conn_id, connector.type, settings)
    except (KeyError, OSError, ValueError, SecretStoreError):
        print("Invalid GitHub connection, local secrets, or expected file.")
        return 2

    ctx = make_context(connector, conn, secrets, deadline=time.monotonic() + BUNDLE_SECONDS)
    result = run_fetch(connector, conn, ref, ctx)
    if result.outcome != RETRIEVED or result.data is None:
        print(f"{result.outcome}: {result.detail}")
        return 1

    digest = hashlib.sha256(result.data).hexdigest()
    print(f"{result.outcome} {result.filename} {result.version} ({len(result.data)} bytes)")
    print(f"Retrieved SHA-256 {digest}")
    print(f"Expected SHA-256  {expected_digest}")
    return 0 if digest == expected_digest else 1


if __name__ == "__main__":
    raise SystemExit(main())
