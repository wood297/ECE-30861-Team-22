"""Verify a Confluence download against a local fixture."""

import argparse
import hashlib
import os
from getpass import getpass
from pathlib import Path

from confluence_client import ConfluenceClient, ConfluenceConnection, ConfluenceError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_id")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--cloud-id", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    try:
        expected = args.fixture.read_bytes()
        token = os.environ.get("CONFLUENCE_API_TOKEN") or getpass("Confluence API token: ")
        client = ConfluenceClient(ConfluenceConnection(args.cloud_id, args.email, token))
        actual = client.fetch_attachment(args.page_id, args.fixture.name)
    except (ConfluenceError, OSError, ValueError) as exc:
        parser.exit(1, f"Confluence verification failed: {exc}\n")
    if actual != expected:
        parser.exit(1, "Downloaded attachment differs from the fixture.\n")
    print(f"Verified {args.fixture.name}: SHA-256 {hashlib.sha256(actual).hexdigest()}")


if __name__ == "__main__":
    main()
