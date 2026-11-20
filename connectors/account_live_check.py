"""Fetch every file test_account.json pins in an external source, and compare it with its
committed copy in tests/fixtures/<type>/. Run it before a cross-team test window.

    CRA_LOCAL_SECRETS=pod.secrets.json python -m connectors.account_live_check --type jenkins

On the server, CRA_SECRET_PREFIX=/team22-cra reads the tokens from SSM instead. The secrets file
is keyed by the connection ids in test_account.json, and must be chmod 600:

    {"connections": {"29b9f99b-52d3-4ef2-acbb-475d8a4f239f": "<Jenkins API token>"}}

A pin passes when the file is retrieved with the copy's exact bytes, or, when there is no copy
(e.g. v1.5-jenkins-missing), when the source confirms it's absent. One JSON line per pin, with
outcome, sha256 and detail, never the file or the token. Exit 0 if every pin passes, 1 if any
doesn't, 2 if the account or the secret store can't be set up.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import artifact_pipeline
from account_config import ACCOUNT_PATH, AccountConfigError, load_account
from connectors import REGISTRY
from connectors.base import NOT_FOUND, RETRIEVED, fill_ref
from safe_http import ByteBudget

COPIES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SECONDS_PER_PIN = 20.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--type", choices=sorted(t for t in REGISTRY if t != "fixtures"),
                        help="check only this connector type")
    parser.add_argument("--account", default=str(ACCOUNT_PATH), help="the account file (default: test_account.json)")
    args = parser.parse_args(argv)

    try:
        account = load_account(args.account)
    except AccountConfigError as error:
        print(f"account: {error}")
        return 2
    secrets = artifact_pipeline.get_secret_store()
    if secrets is None:
        print(f"no secret store: set {artifact_pipeline.LOCAL_SECRETS_ENV} (a chmod 600 file) "
              f"or {artifact_pipeline.SECRET_PREFIX_ENV}")
        return 2

    checked = failed = 0
    for (product, release), pinned in sorted(account.releases.items()):
        for category, entry in sorted(pinned.pins.items()):
            conn = account.connections[entry["conn"]]
            if conn.type == "fixtures" or (args.type and conn.type != args.type):
                continue
            connector = REGISTRY[conn.type]
            ref = connector.validate_ref(fill_ref({k: v for k, v in entry.items() if k != "conn"}, product, release))
            copies = [COPIES / conn.type / name for name in ref.get("paths") or [ref.get("filename")]]
            expected = next((path.read_bytes() for path in copies if path.is_file()), None)
            result = artifact_pipeline.run_connector(
                connector, conn, ref, deadline=time.monotonic() + SECONDS_PER_PIN,
                budget=ByteBudget(artifact_pipeline.CONNECTOR_BUDGET_BYTES), secrets=secrets)
            retrieved = result.outcome == RETRIEVED and result.data is not None
            got = hashlib.sha256(result.data or b"").hexdigest() if retrieved else None
            if expected is None:
                ok = result.outcome == NOT_FOUND
            else:
                ok = got == hashlib.sha256(expected).hexdigest()
            checked += 1
            failed += not ok
            print(json.dumps({"release": f"{product}/{release}", "category": category, "source": connector.display_name,
                              "expected": "absent" if expected is None else hashlib.sha256(expected).hexdigest(),
                              "outcome": result.outcome, "sha256": got, "version": result.version,
                              "detail": result.detail, "ok": ok}))
    print(f"{checked - failed}/{checked} pins match")
    return 0 if checked and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
