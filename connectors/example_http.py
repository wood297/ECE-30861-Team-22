"""A complete HTTP connector, written as the example to copy (Phase 5 plan, §11).

It reads a file from a plain HTTPS file server that takes a bearer token:

    GET <base_url>/<path>      Authorization: Bearer <token>

It isn't registered, since no real source works this way; it exists to show every part of the
contract in one place, and tests/test_connector_example_http.py runs the contract tests on it.
GitHub, Confluence and Jenkins differ only in the requests they make and how they read the
answers.
"""
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from connectors.base import (LIST, SECRET, URL, BaseConnector, FetchContext, FetchResult, Field, SettingsError,
                             result_for_http_error, result_for_status)
from safe_http import SafeHttpError
from tenant_store import Connection

_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"


class ExampleHttpConnector(BaseConnector):
    # The registry key, and the name shown to companies and in external_source
    type = "example-http"
    display_name = "Example file server"

    # What an admin enters once for a connection. The secret field's value goes to SecretStore,
    # never into the settings; fetch() reads it with ctx.secret().
    settings_fields = (
        Field("base_url", "Server address", kind=URL, help="e.g. https://files.example.com/docs"),
        Field("token", "Access token", kind=SECRET),
    )
    # What a product or release pins. {product} and {release} are filled in before fetch().
    ref_fields = (
        Field("paths", "File paths, tried in order", kind=LIST, pattern=rf"{_SEGMENT}(/{_SEGMENT}){{0,5}}"),
    )

    def validate_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_settings(settings)   # required fields, https://, lengths
        parts = urlsplit(clean["base_url"])
        if parts.port not in (None, 443) or parts.query or parts.fragment or parts.username:
            raise SettingsError("base_url", "must be a plain https:// address on the standard port")
        return clean

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        # Exactly the server's host. safe_http refuses anything else, including a redirect target
        return [urlsplit(settings["base_url"]).hostname or ""]

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        headers = {"Authorization": f"Bearer {ctx.secret().reveal()}"}
        base = conn.settings["base_url"]
        for path in ref["paths"]:
            url = f"{base}/{quote(path)}"
            try:
                response = ctx.http.get(url, headers=headers)
            except SafeHttpError as error:
                return result_for_http_error(error, self.display_name)
            if response.status == 404:
                ctx.log.info("no file at %s, trying the next path", path)
                continue   # only a confirmed absence moves on to the next candidate
            failure = result_for_status(response.status, self.display_name)
            if failure:
                return failure
            return FetchResult.retrieved(response.body, path.rsplit("/", 1)[-1],
                                         version=response.headers.get("etag"))
        return FetchResult.not_found(f"{self.display_name} has none of the pinned paths.")
