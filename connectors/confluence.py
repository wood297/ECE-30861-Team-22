"""Confluence Cloud attachment retrieval through the shared connector contract."""
import base64
import json
import re
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

from connectors.base import (INTEGER, SECRET, BaseConnector, FetchContext, FetchResult, Field,
                             SettingsError, result_for_http_error, result_for_status)
from safe_http import SafeHttpError
from tenant_store import Connection


class ConfluenceConnector(BaseConnector):
    type = "confluence"
    display_name = "Confluence"
    settings_fields = (
        Field("cloud_id", "Cloud ID", pattern=r"[a-zA-Z0-9-]{1,64}"),
        Field("email", "Atlassian account email", pattern=r"[^\s:]+@[^\s:]+"),
        Field("token", "Scoped API token", kind=SECRET),
    )
    ref_fields = (
        Field("page_id", "Page ID", pattern=r"[0-9]{1,20}"),
        Field("filename", "Attachment filename", pattern=r"[^/\\]{1,200}"),
        Field("version", "Attachment version", kind=INTEGER),
    )

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_ref(ref)
        version = clean["version"]
        if isinstance(version, int) and version < 1:
            raise SettingsError("version", "must be at least 1")
        return clean

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        return ["api.atlassian.com", "api.media.atlassian.com"]

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        encoded = base64.b64encode(f"{conn.settings['email']}:{ctx.secret().reveal()}".encode()).decode()
        ctx.masker.add(encoded)
        headers = {"Authorization": "Basic " + encoded}
        base = f"https://api.atlassian.com/ex/confluence/{conn.settings['cloud_id']}"
        listing = f"{base}/wiki/api/v2/pages/{ref['page_id']}/attachments"
        url = listing
        visited: set[str] = set()
        matches = []
        try:
            while url:
                if url in visited or len(visited) >= 100:
                    return FetchResult.source_error("Confluence attachment pagination did not complete.")
                visited.add(url)
                response = ctx.http.get(url, headers=headers)
                failure = result_for_status(response.status, self.display_name)
                if failure:
                    return failure
                payload = json.loads(response.body)
                if not isinstance(payload["results"], list):
                    raise ValueError
                for item in payload["results"]:
                    if item["title"] == ref["filename"]:
                        matches.append(item["id"])
                next_link = payload.get("_links", {}).get("next")
                url = self._next_url(base, listing, url, next_link) if next_link else ""
            if not matches:
                return FetchResult.not_found("Confluence has no attachment with the pinned filename.")
            if len(matches) != 1:
                return FetchResult.source_error("Confluence returned multiple attachments with the pinned filename.")
            attachment_id = matches[0]
            if not isinstance(attachment_id, str) or not re.fullmatch(r"(?:att)?[0-9]+", attachment_id):
                raise ValueError
            download = (f"{base}/wiki/rest/api/content/{ref['page_id']}/child/attachment/"
                        f"{attachment_id}/download?{urlencode({'version': ref['version']})}")
            response = ctx.http.get(download, headers=headers)
            if response.status == 400:
                # The download API also uses 400 for an absent version. Confirm
                # absence with the version endpoint before reporting NOT_FOUND.
                version_url = (f"{base}/wiki/api/v2/attachments/{attachment_id}/versions/"
                               f"{ref['version']}")
                version_response = ctx.http.get(version_url, headers=headers)
                if version_response.status == 404:
                    return FetchResult.not_found("Confluence has no attachment at the pinned version.")
                failure = result_for_status(version_response.status, self.display_name)
                if failure:
                    return failure
                return FetchResult.source_error("Confluence refused the pinned attachment download.")
            if response.status == 302:
                target = response.headers.get("location", "")
                parts = urlsplit(target)
                if (parts.scheme != "https" or parts.hostname != "api.media.atlassian.com"
                        or parts.port not in (None, 443) or parts.username or parts.fragment):
                    return FetchResult.source_error("Confluence returned an unexpected download destination.")
                response = ctx.http.get(target)
            failure = result_for_status(response.status, self.display_name)
            if failure:
                return failure
            return FetchResult.retrieved(response.body, ref["filename"], version=str(ref["version"]))
        except SafeHttpError as error:
            return result_for_http_error(error, self.display_name)
        except (ValueError, TypeError, KeyError, AttributeError):
            return FetchResult.source_error("Confluence returned an invalid attachment response.")

    @staticmethod
    def _next_url(base: str, listing: str, current: str, link: str) -> str:
        if link.startswith("/wiki/"):
            link = base + link
        target = urljoin(current, link)
        if target.split("?", 1)[0] != listing:
            raise ValueError("Unexpected pagination URL")
        return target
