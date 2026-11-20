"""Verify a pinned Confluence attachment through the production connector contract."""
import argparse
import hashlib
import time
import uuid
from getpass import getpass
from pathlib import Path

from connectors import make_context
from connectors.base import RETRIEVED, run_fetch
from connectors.confluence import ConfluenceConnector
from secret_store import InMemorySecretStore
from tenant_store import Connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--page-id", default="622599")
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--fixture", type=Path,
                        default=Path(__file__).parent / "tests/fixtures/confluence/risk_assessment.md")
    args = parser.parse_args()
    connector = ConfluenceConnector()
    settings = connector.validate_settings({"cloud_id": args.cloud_id, "email": args.email})
    ref = connector.validate_ref({"page_id": args.page_id, "filename": args.fixture.name,
                                  "version": args.version})
    expected = args.fixture.read_bytes()
    conn = Connection(str(uuid.uuid4()), str(uuid.uuid4()), "confluence", settings)
    secrets = InMemorySecretStore()
    secrets.put_connection_secret(conn.org_id, conn.conn_id, getpass("Confluence API token: "))
    ctx = make_context(connector, conn, secrets, deadline=time.monotonic() + 30)
    result = run_fetch(connector, conn, ref, ctx)
    if result.outcome != RETRIEVED:
        parser.exit(1, f"{result.outcome}: {result.detail}\n")
    if result.data != expected:
        parser.exit(1, "Downloaded bytes differ from the reference fixture.\n")
    print(f"Verified {result.filename} version {result.version}: "
          f"SHA-256 {hashlib.sha256(expected).hexdigest()}")


if __name__ == "__main__":
    main()
