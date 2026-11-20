"""Fetch one artifact archived by a pinned Jenkins build (docs/PHASE5_JENKINS_PLAN_V2.md, §3).

    settings: url   https://jenkins.example.com (a path prefix such as /jenkins is fine)
              user  the Jenkins user name
              token an API token for that user (secret)
    ref:      job   folders and job, e.g. pod/cra-test-logs
              build a build number; never lastSuccessfulBuild, so a ref always returns the same bytes
              paths artifact paths relative to the build's archive, tried in order

Two GETs: the build's artifact listing, then the chosen artifact. The listing is complete, so a
path missing from it is a confirmed absence (NOT_FOUND), not an outage.
"""
import base64
import json
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from connectors.base import (INTEGER, LIST, SECRET, URL, BaseConnector, FetchContext, FetchResult, Field,
                             MissingSecret, SettingsError, result_for_http_error, result_for_status)
from safe_http import Response, SafeHttpError
from tenant_store import Connection

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
MAX_BUILD = 10**9
# Only the fields we read, so a build with many artifacts still gives a small answer
_LISTING_QUERY = "tree=building,result,artifacts%5BrelativePath%5D"


class JenkinsConnector(BaseConnector):
    type = "jenkins"
    display_name = "Jenkins"
    settings_fields = (
        Field("url", "Jenkins address", kind=URL, help="e.g. https://jenkins.example.com"),
        Field("user", "User name", pattern=r"[^\s:]{1,100}"),
        Field("token", "API token", kind=SECRET),
    )
    ref_fields = (
        Field("job", "Job path", pattern=rf"{_SEGMENT}(/{_SEGMENT}){{0,4}}",
              help="Folders and job name, e.g. pod/cra-test-logs"),
        Field("build", "Build number", kind=INTEGER, help="Mark the build 'Keep this build forever'"),
        Field("paths", "Artifact paths, tried in order", kind=LIST, pattern=rf"{_SEGMENT}(/{_SEGMENT}){{0,9}}",
              help="e.g. artifacts/{product}/{release}/test_logs.md"),
    )

    def validate_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_settings(settings)
        try:
            parts = urlsplit(clean["url"])
            port = parts.port
        except ValueError:
            raise SettingsError("url", "is not a valid address") from None
        if (not parts.hostname or port not in (None, 443) or parts.query or parts.fragment
                or parts.username is not None or parts.password is not None):
            raise SettingsError("url", "must be a plain https:// address on the standard port")
        return clean

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_ref(ref)
        build = clean["build"]
        # A placeholder passes the INTEGER check, but a build number must be fixed (rule 8)
        if isinstance(build, bool) or not isinstance(build, int) or not 1 <= build <= MAX_BUILD:
            raise SettingsError("build", "must be a fixed build number of at least 1")
        if any(".." in part for part in clean["job"].split("/")):
            raise SettingsError("job", "must not contain '..'")
        if any(".." in path for path in clean["paths"]):
            raise SettingsError("paths", "must not contain '..'")
        return clean

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        return [urlsplit(settings["url"]).hostname or ""]

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        job, build = ref["job"], ref["build"]
        try:
            token = ctx.secret().reveal()
        except MissingSecret:
            return FetchResult.auth_failed("The Jenkins connection has no stored API token.")
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            return FetchResult.auth_failed("The Jenkins connection's API token is not valid.")
        encoded = base64.b64encode(f"{conn.settings['user']}:{token}".encode()).decode()
        ctx.masker.add(encoded)
        headers = {"Authorization": "Basic " + encoded, "Accept": "application/json"}
        job_url = conn.settings["url"].rstrip("/") + "".join(
            f"/job/{quote(part, safe='')}" for part in job.split("/"))
        build_url = f"{job_url}/{int(build)}"

        try:
            response = ctx.http.get(f"{build_url}/api/json?{_LISTING_QUERY}", headers=headers)
            if response.status == 404:
                return FetchResult.not_found(f"Jenkins has no build {build} of {job}, or this account can't see it.")
            failure = self._failure(response)
            if failure:
                return failure
            listing = _parse_listing(response.body)
            if listing is None:
                return FetchResult.source_error("Jenkins returned an invalid build listing.")
            building, archived = listing
            if building:
                return FetchResult.unavailable(f"Jenkins build {build} of {job} is still running.")
            path = next((candidate for candidate in ref["paths"] if candidate in archived), None)
            if path is None:
                ctx.log.info("build %s of %s archives none of the pinned paths", build, job)
                return FetchResult.not_found(f"Jenkins build {build} of {job} has none of the pinned artifact paths.")

            response = ctx.http.get(f"{build_url}/artifact/{quote(path, safe='/')}", headers=headers)
            if response.status == 404:
                return FetchResult.not_found(f"Jenkins build {build} of {job} no longer has the pinned artifact.")
            failure = self._failure(response)
            if failure:
                return failure
        except SafeHttpError as error:
            return result_for_http_error(error, self.display_name)
        return FetchResult.retrieved(response.body, path.rsplit("/", 1)[-1], version=f"{job}#{build}")

    def _failure(self, response: Response) -> FetchResult | None:
        if response.status == 403 and _blocked_by_cloudflare(response):
            # Still AUTH_FAILED, like every 403, but say who refused: Jenkins never saw the request
            return FetchResult.auth_failed("Cloudflare blocked the request before it reached Jenkins (403).")
        return result_for_status(response.status, self.display_name)


def _blocked_by_cloudflare(response: Response) -> bool:
    """A 403 answered by Cloudflare itself. Requests that reach Jenkins also say
    server: cloudflare, but carry Jenkins' x-jenkins header."""
    return response.headers.get("server", "").lower() == "cloudflare" and "x-jenkins" not in response.headers


def _parse_listing(body: bytes) -> tuple[bool, set[str]] | None:
    """(building, archived relative paths) from a build's api/json, or None if it's malformed."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    building = payload.get("building", False)
    artifacts = payload.get("artifacts")
    if not isinstance(building, bool) or not isinstance(artifacts, list):
        return None
    paths: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("relativePath"), str):
            return None
        paths.add(artifact["relativePath"])
    return building, paths
