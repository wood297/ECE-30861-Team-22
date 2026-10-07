import json
import urllib.error
import urllib.request


class Response:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body or b""
        self.headers = headers or {}

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class HttpClient:
    """Sends requests to a deployed service. Paths are resolved against base_url."""

    def __init__(self, base_url, token=None, timeout=10):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.default_headers = {"Accept": "application/json"}
        if token:
            # Phase 2 section 5.3: cross-team tests send a shared token in the headers
            self.default_headers["Authorization"] = f"Bearer {token}"

    def request(self, method, path, json_body=None, raw_body=None, headers=None):
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url + path.lstrip("/")

        all_headers = dict(self.default_headers)
        all_headers.update(headers or {})
        data = raw_body
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return Response(resp.status, resp.read(), dict(resp.headers))
        except urllib.error.HTTPError as err:
            return Response(err.code, err.read(), dict(err.headers))


class WSGIClient:
    """Drives a Flask app in-process, so the same suite runs inside pytest without a server."""

    def __init__(self, app):
        self._client = app.test_client()

    def request(self, method, path, json_body=None, raw_body=None, headers=None):
        resp = self._client.open(path, method=method, json=json_body, data=raw_body, headers=headers)
        return Response(resp.status_code, resp.get_data(), dict(resp.headers))
