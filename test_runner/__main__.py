"""Command-line runner: python -m test_runner --url http://localhost:5000

Does what the tester service does in one step: creates a session ID, calls the
system's POST /test/setup with it (unless --no-setup), runs the acceptance suite,
reads code coverage from the system's GET /test/report and prints the report.

Exit codes: 0 nothing failed, 1 a test failed or errored, 2 system setup failed.
"""
import argparse
import json
import sys
import uuid

from test_runner.client import HttpClient
from test_runner.session import SetupError, setup_target
from test_runner.suite import run_suite

SYMBOLS = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "error": "ERR "}


def print_report(report, verbose=False):
    print(f"Acceptance suite against {report['target_url']} "
          f"(session {report['session_id']}, spec {report['spec_version']})\n")
    for result in report["results"]:
        source = f" [source: {result['source']}]" if result["outcome"] == "error" else ""
        print(f"[{SYMBOLS[result['outcome']]}] {result['test_id']}  {result['title']}{source}")
        if result["outcome"] in ("skip", "error"):
            print(f"         {result['message']}")
        for check in result["checks"]:
            if verbose or check["passed"] is False:
                mark = {True: "ok", False: "FAILED", None: "n/a"}[check["passed"]]
                detail = f" ({check['detail']})" if check["detail"] else ""
                print(f"         - {mark}: {check['description']}{detail}")
    print("\nCoverage:")
    for kind, entry in report["coverage"].items():
        counts = "n/a" if entry["covered"] is None else f"{entry['covered']}/{entry['total']}"
        print(f"  {kind:<9} {counts:<6} {entry['basis']}")
    s = report["summary"]
    print(f"\n{s['pass']} passed, {s['fail']} failed, {s['skip']} skipped, "
          f"{s['error']} errors in {report['duration_ms'] / 1000:.1f}s")


def main(argv=None, make_client=HttpClient):
    parser = argparse.ArgumentParser(description="Run the pod acceptance suite against a deployed system.")
    parser.add_argument("--url", required=True, help="base URL of the system under test")
    parser.add_argument("--token", help="shared auth token, sent as a Bearer token")
    parser.add_argument("--poll-timeout", type=float, default=5.0,
                        help="seconds to wait for a bundle to reach a terminal status (default 5)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="run only these tests, e.g. AC-02 AC-03")
    parser.add_argument("--no-setup", action="store_true",
                        help="skip the system's /test/setup (code coverage will then be unavailable)")
    parser.add_argument("--json", action="store_true", help="print the raw JSON report")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every check, not only failures")
    args = parser.parse_args(argv)

    client = make_client(args.url, token=args.token)
    session_id = str(uuid.uuid4())
    if not args.no_setup:
        try:
            setup_target(client, session_id)
        except SetupError as exc:
            print(f"System setup at {args.url} failed (HTTP {exc.status}, source {exc.source}): {exc.message}",
                  file=sys.stderr)
            return 2

    report = run_suite(client, session_id=None if args.no_setup else session_id, target=args.url,
                       poll_timeout=args.poll_timeout, only=set(args.only) if args.only else None)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, verbose=args.verbose)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
