"""Read Confluence attachments using a connection supplied by the caller."""

import base64
import json
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ConfluenceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ConfluenceConnection:
    cloud_id: str
    email: str
    token: str = field(repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9-]+", self.cloud_id):
            raise ValueError("Invalid Confluence Cloud ID")
        if not self.email or ":" in self.email or not self.token:
            raise ValueError("Confluence account email and API token are required")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ConfluenceClient:
    """Retrieve exact filenames from a configured Confluence Cloud site."""

    def __init__(self, connection: ConfluenceConnection, opener=None):
        self._base = f"https://api.atlassian.com/ex/confluence/{connection.cloud_id}"
        encoded = base64.b64encode(f"{connection.email}:{connection.token}".encode())
        self._authorization = "Basic " + encoded.decode("ascii")
        self._opener = opener if opener is not None else build_opener(_NoRedirect())

    def _get(self, url: str, *, authenticated: bool, allow_redirect: bool = False):
        request = Request(url)
        if authenticated:
            request.add_header("Authorization", self._authorization)
        try:
            with self._opener.open(request, timeout=10) as response:
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise ConfluenceError("too_large", "Confluence response exceeds 1 MiB")
                return body
        except HTTPError as exc:
            with exc:
                if allow_redirect and exc.code == 302 and exc.headers.get("Location"):
                    return exc.headers["Location"]
                raise ConfluenceError(
                    f"http_{exc.code}", f"Confluence returned HTTP {exc.code}"
                ) from None
        except (URLError, OSError):
            raise ConfluenceError("network", "Confluence request failed") from None

    def fetch_attachment(self, page_id: str, filename: str) -> bytes:
        """Return original bytes or raise a failure with a stable diagnostic code."""
        if not re.fullmatch(r"[0-9]+", page_id):
            raise ValueError("Invalid Confluence page ID")
        path = f"/wiki/api/v2/pages/{page_id}/attachments"
        url = self._base + path
        seen = set()
        matches = []
        while url:
            if url in seen or len(seen) >= 100:
                raise ConfluenceError("pagination", "Confluence pagination did not complete")
            seen.add(url)
            try:
                payload = json.loads(self._get(url, authenticated=True))
                results = payload["results"]
                if not isinstance(results, list):
                    raise ValueError
                matches.extend(item["id"] for item in results if item["title"] == filename)
                next_link = payload.get("_links", {}).get("next")
                url = self._next_page(url, path, next_link) if next_link else ""
            except (AttributeError, KeyError, TypeError, ValueError):
                raise ConfluenceError("response", "Invalid Confluence attachment listing") from None

        if not matches:
            raise ConfluenceError("missing", "Attachment is absent from the Confluence page")
        if len(matches) != 1:
            raise ConfluenceError("ambiguous", "Multiple Confluence attachments match the filename")
        attachment_id = matches[0]
        if not isinstance(attachment_id, str) or not re.fullmatch(r"(?:att)?[0-9]+", attachment_id):
            raise ConfluenceError("response", "Invalid Confluence attachment ID")
        download = f"{self._base}/wiki/rest/api/content/{page_id}/child/attachment/{attachment_id}/download"
        result = self._get(download, authenticated=True, allow_redirect=True)
        if isinstance(result, bytes):
            return result
        try:
            target = urlsplit(result)
            host = target.hostname or ""
            allowed = any(host == suffix or host.endswith("." + suffix)
                          for suffix in ("atlassian.com", "atlassian.net"))
            if target.scheme != "https" or not allowed or target.username is not None or target.port not in (None, 443):
                raise ValueError
        except ValueError:
            raise ConfluenceError("redirect", "Unexpected Confluence download destination") from None
        return self._get(result, authenticated=False)

    def _next_page(self, current: str, path: str, next_link: str) -> str:
        if next_link.startswith(path + "?"):
            next_link = self._base + next_link
        url = urljoin(current, next_link)
        if url.split("?", 1)[0] != self._base + path:
            raise ValueError("Unexpected pagination destination")
        return url
