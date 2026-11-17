"""Run one connector fetch against a real source from your laptop, without AWS or the pipeline.

    CRA_LOCAL_SECRETS=confluence.secrets.json \\
        python -m connectors.try_fetch confluence.local.json --product prd-88 --release v5.0

The connection file (keep it out of git: *.local.json is ignored):

    {"type": "confluence",
     "conn_id": "6f1c1d2e-0000-4000-8000-000000000001",
     "settings": {"site": "whcora.atlassian.net", "email": "you@example.com"},
     "ref": {"page_id": "622599", "filename": "risk_assessment.md"}}

The secrets file (chmod 600, also ignored), keyed by the same conn_id:

    {"connections": {"6f1c1d2e-0000-4000-8000-000000000001": "<token>"}}

The fetch runs through the same validation, safe_http limits and run_fetch() safety net as on
the server. It prints the outcome, file name, size and SHA-256, never the file or the token;
--save writes the file out.
"""
import argparse
import hashlib
import ipaddress
import json
import logging
import os
import sys
import time
import uuid

from connectors import REGISTRY, make_context
from connectors.base import RETRIEVED, Connector, SettingsError, fill_ref, run_fetch
from connectors.example_http import ExampleHttpConnector
from secret_store import LocalFileSecretStore, SecretStoreError
from tenant_store import Connection

LOCAL_ORG = "00000000-0000-4000-8000-000000000000"
BUNDLE_SECONDS = 20.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("connection", help="the connection JSON file")
    parser.add_argument("--product", default="prd-88")
    parser.add_argument("--release", default="v5.0")
    parser.add_argument("--save", metavar="PATH", help="write the retrieved file here")
    parser.add_argument("--private-network", action="append", default=[], metavar="CIDR",
                        help="allow an internal network, e.g. a Jenkins on your LAN (never a company setting)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the connector's log lines")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(message)s")

    registry: dict[str, Connector] = {**REGISTRY, ExampleHttpConnector.type: ExampleHttpConnector()}
    with open(args.connection, encoding="utf-8") as handle:
        spec = json.load(handle)
    connector = registry.get(spec.get("type"))
    if connector is None:
        print(f"unknown connector type {spec.get('type')!r}; registered: {', '.join(sorted(registry))}")
        return 2
    try:
        settings = connector.validate_settings(spec.get("settings") or {})
        ref = fill_ref(connector.validate_ref(spec.get("ref") or {}), args.product, args.release)
    except SettingsError as error:
        print(f"invalid {error.field}: {error.message}")
        return 2

    secrets = None
    if os.environ.get("CRA_LOCAL_SECRETS"):
        try:
            secrets = LocalFileSecretStore(os.environ["CRA_LOCAL_SECRETS"])
        except (OSError, ValueError, SecretStoreError) as error:
            print(f"CRA_LOCAL_SECRETS: {error}")
            return 2
    conn = Connection(LOCAL_ORG, spec.get("conn_id") or str(uuid.uuid4()), connector.type, settings)
    networks = [ipaddress.ip_network(cidr) for cidr in args.private_network]
    ctx = make_context(connector, conn, secrets, deadline=time.monotonic() + BUNDLE_SECONDS,
                       private_networks=networks)

    started = time.monotonic()
    result = run_fetch(connector, conn, ref, ctx)
    print(f"outcome:  {result.outcome}  ({time.monotonic() - started:.2f}s)")
    print(f"ref:      {json.dumps(ref)}")
    if result.version:
        print(f"version:  {result.version}")
    if result.outcome != RETRIEVED:
        print(f"detail:   {result.detail}")
        return 1
    data = result.data or b""
    print(f"filename: {result.filename}")
    print(f"bytes:    {len(data)}")
    print(f"sha256:   {hashlib.sha256(data).hexdigest()}")
    if args.save:
        with open(args.save, "wb") as handle:
            handle.write(data)
        print(f"saved:    {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
