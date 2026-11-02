"""Checks that a deployed system is the expected build and can produce a bundle.

    python3 deploy/smoke_test.py https://team22.13-58-35-224.sslip.io/system main@abc1234

Exit 0: every check passed. Exit 1: a check failed, with the reason on stderr. Uses only the
standard library, so it runs without installing anything. TLS verification stays on, because
that's how other teams' clients connect, so a bad certificate chain fails here too.
"""

import io
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import zipfile

PRODUCT = "prd-88"
RELEASE = "v1.2"
EXPECTED_FILES = {"sbom.md", "risk_assessment.md", "test_logs.md", "user_instructions.md", "manifest.json"}
REQUEST_TIMEOUT = 10
_opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))


class SmokeTestFailure(Exception):
    pass


def request(url, data=None):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    try:
        with _opener.open(urllib.request.Request(url, data=data, headers=headers), timeout=REQUEST_TIMEOUT) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        raise SmokeTestFailure(f"{url} returned {error.code}") from None
    except (urllib.error.URLError, OSError) as error:
        raise SmokeTestFailure(f"{url} could not be reached: {error}") from None


def check_version(base_url, expected, wait_seconds):
    # Keep trying for a while, because the proxy can take a moment to reach a restarted app
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            _, body = request(f"{base_url}/version")
            actual = json.loads(body).get("version")
            if actual == expected:
                return
            problem = f"running version is {actual}, expected {expected}"
        except (SmokeTestFailure, ValueError, AttributeError) as error:
            problem = str(error)
        if time.monotonic() >= deadline:
            raise SmokeTestFailure(problem)
        time.sleep(2)


def create_bundle(base_url):
    payload = json.dumps({"product_id": PRODUCT, "release_version": RELEASE}).encode()
    status, body = request(f"{base_url}/bundles", payload)
    if status != 202:
        raise SmokeTestFailure(f"POST /bundles returned {status}, expected 202")
    return json.loads(body)["bundle_id"]


def wait_for_bundle(base_url, bundle_id, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        _, body = request(f"{base_url}/bundles/{bundle_id}")
        bundle = json.loads(body)
        status = bundle.get("status")
        if status == "COMPLETED":
            return bundle
        if status not in ("PENDING", "PROCESSING"):
            raise SmokeTestFailure(f"bundle ended as {status}, expected COMPLETED")
        if time.monotonic() >= deadline:
            raise SmokeTestFailure(f"bundle was still {status} after {wait_seconds} seconds")
        time.sleep(1)


def check_download(bundle):
    download_url = bundle.get("download_url")
    if not download_url:
        raise SmokeTestFailure("completed bundle has no download_url")
    _, body = request(download_url)
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = set(archive.namelist())
            corrupt = archive.testzip()
    except zipfile.BadZipFile:
        raise SmokeTestFailure("download isn't a valid zip") from None
    if corrupt:
        raise SmokeTestFailure(f"{corrupt} in the zip is corrupt")
    if names != EXPECTED_FILES:
        raise SmokeTestFailure(f"zip holds {sorted(names)}, expected {sorted(EXPECTED_FILES)}")


def run(base_url, expected_version, wait_seconds=60):
    base_url = base_url.rstrip("/")
    check_version(base_url, expected_version, wait_seconds)
    bundle_id = create_bundle(base_url)
    bundle = wait_for_bundle(base_url, bundle_id, wait_seconds)
    check_download(bundle)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: smoke_test.py <system base url> <expected version>", file=sys.stderr)
        return 2
    try:
        run(args[0], args[1])
    except (SmokeTestFailure, ValueError, KeyError, TypeError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        return 1
    print(f"Smoke test passed for {args[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
