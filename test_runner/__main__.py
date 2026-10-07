"""Command-line runner: python -m test_runner --url http://localhost:5000

Calls the target's POST /test/setup (unless --no-setup), runs the acceptance
suite, prints the report and exits 0 when nothing failed, 1 otherwise.
"""
import argparse
import json
import sys
import urllib.error

from test_runner.client import HttpClient
from test_runner.suite import run_suite

SYMBOLS = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP", "error": "ERR "}


def print_report(report, verbose=False):
    print(f"Acceptance suite against {report['target']} (spec {report['spec_version']})\n")
    for result in report["results"]:
        print(f"[{SYMBOLS[result['outcome']]}] {result['id']}  {result['title']}")
        if result["reason"]:
            print(f"         {result['reason']}")
        for check in result["checks"]:
            if verbose or check["passed"] is False:
                mark = {True: "ok", False: "FAILED", None: "n/a"}[check["passed"]]
                detail = f" ({check['detail']})" if check["detail"] else ""
                print(f"         - {mark}: {check['description']}{detail}")
    s = report["summary"]
    print(f"\n{s['passed']} passed, {s['failed']} failed, {s['skipped']} skipped, "
          f"{s['error']} errors in {report['duration_ms'] / 1000:.1f}s")


def main(argv=None, make_client=HttpClient):
    parser = argparse.ArgumentParser(description="Run the pod acceptance suite against a deployed service.")
    parser.add_argument("--url", required=True, help="base URL of the system under test")
    parser.add_argument("--token", help="shared auth token, sent as a Bearer token")
    parser.add_argument("--poll-timeout", type=float, default=5.0,
                        help="seconds to wait for a bundle to reach a terminal status (default 5)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="run only these criteria, e.g. AC-02 AC-03")
    parser.add_argument("--no-setup", action="store_true", help="do not call the target's /test/setup first")
    parser.add_argument("--json", action="store_true", help="print the raw JSON report")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every check, not only failures")
    args = parser.parse_args(argv)

    client = make_client(args.url, token=args.token)
    if not args.no_setup:
        try:
            client.request("POST", "/test/setup")
        except (urllib.error.URLError, OSError) as exc:
            print(f"Target {args.url} is unreachable: {exc}", file=sys.stderr)
            return 2

    report = run_suite(client, target=args.url, poll_timeout=args.poll_timeout,
                       only=set(args.only) if args.only else None)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, verbose=args.verbose)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
