"""Shared contract tests every connector runs (Phase 5 plan, §11.3).

Subclass one of the classes below in tests/test_connector_<type>.py; pytest then runs every
contract test on your connector. Requests go through the real safe_http checks to a FakeSource
instead of the network, so the tests are fast and need no tokens.

    class TestConfluenceContract(HttpConnectorContract):
        connector = ConfluenceConnector()
        settings = {"site": "acme.atlassian.net", "email": "bot@acme.example"}
        ref = {"page_id": "622599", "filename": "risk_assessment.md"}
        expected_filename = "risk_assessment.md"

        def serve_file(self, request):      # the source when the file exists
            ...return FakeResponse(200, b'...') for each request your connector makes
        def serve_missing(self, request):   # the source when it doesn't
            ...

See tests/test_connector_example_http.py for a complete example.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from connectors import REGISTRY, make_context
from connectors.base import (AUTH_FAILED, NOT_FOUND, OUTCOMES, RETRIEVED, UNAVAILABLE, Connector, FetchResult,
                             MissingSecret, SettingsError, fill_ref, run_fetch)
from safe_http import DEFAULT_MAX_BYTES
from secret_store import InMemorySecretStore
from tenant_store import Connection

PUBLIC_ADDRESS = "93.184.215.14"
METADATA_ADDRESS = "169.254.169.254"
SECRET = "contract-test-secret-6f1c1d2e9a"


# A fake network for safe_http

@dataclass
class FakeRequest:
    method: str
    host: str
    address: str
    target: str            # path and query, e.g. /wiki/api/v2/pages/1/attachments?limit=50
    headers: dict[str, str]
    body: bytes | None

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.target}"


@dataclass
class FakeResponse:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, status: int = 200) -> "FakeResponse":
        import json
        return cls(status, json.dumps(data).encode(), {"Content-Type": "application/json"})

    @classmethod
    def redirect(cls, location: str, status: int = 302) -> "FakeResponse":
        return cls(status, b"", {"Location": location})


Handler = Callable[[FakeRequest], FakeResponse]


class _Reply:
    def __init__(self, response: FakeResponse):
        self.status = response.status
        self._body = response.body
        self._headers = list(response.headers.items())

    def read(self, amount: int) -> bytes:
        chunk, self._body = self._body[:amount], self._body[amount:]
        return chunk

    def getheaders(self):
        return self._headers


class FakeSource:
    """Answers safe_http's requests with handler(request). The handler may raise TimeoutError
    or OSError to act as a network failure. dns maps host names to addresses (public by default)."""

    def __init__(self, handler: Handler, dns: dict[str, list[str]] | None = None):
        self.handler = handler
        self.dns = dns or {}
        self.requests: list[FakeRequest] = []
        self.opened: list[tuple[str, str]] = []

    def resolve(self, host: str, port: int) -> list[str]:
        return self.dns.get(host, [PUBLIC_ADDRESS])

    def connect(self, host: str, address: str, port: int, timeout: float):
        self.opened.append((host, address))
        source = self

        class Connection:
            def request(self, method, target, body=None, headers=None):
                self.req = FakeRequest(method, host, address, target, dict(headers or {}), body)
                source.requests.append(self.req)

            def getresponse(self):
                return _Reply(source.handler(self.req))

            def close(self):
                pass
        return Connection()

    def http_options(self) -> dict[str, Any]:
        return {"resolver": self.resolve, "connection_factory": self.connect}


# The contract

class ConnectorContract:
    """Tests that hold for every connector. Subclasses set the attributes below."""
    connector: Connector
    settings: dict[str, Any] = {}
    ref: dict[str, Any] = {}           # as a release pins it; placeholders allowed
    product = "prd-88"
    release = "v5.0"
    expected_filename: str | None = None
    expected_bytes: bytes | None = None
    registered = True                  # False only for the unregistered example connector

    # Helpers

    @property
    def needs_secret(self) -> bool:
        return any(f.secret for f in self.connector.settings_fields)

    def make_conn(self, secrets: InMemorySecretStore | None = None) -> Connection:
        conn = Connection(str(uuid.uuid4()), str(uuid.uuid4()), self.connector.type,
                          self.connector.validate_settings(self.settings))
        if secrets is not None and self.needs_secret:
            path = secrets.put_connection_secret(conn.org_id, conn.conn_id, SECRET)
            conn = Connection(conn.org_id, conn.conn_id, conn.type, conn.settings, secret_path=path)
        return conn

    def concrete_ref(self) -> dict[str, Any]:
        return fill_ref(self.connector.validate_ref(self.ref), self.product, self.release)

    def fetch(self, source: "FakeSource | None" = None, *, store_secret: bool = True, deadline: float | None = None,
              direct: bool = False, max_bytes: int = DEFAULT_MAX_BYTES) -> FetchResult:
        """Fetch through run_fetch (direct=False) or by calling connector.fetch() itself."""
        secrets = InMemorySecretStore()
        conn = self.make_conn(secrets if store_secret else None)
        ctx = make_context(self.connector, conn, secrets, deadline=deadline, max_bytes=max_bytes,
                           http_options=source.http_options() if source else None)
        if direct:
            return self.connector.fetch(conn, self.concrete_ref(), ctx)
        return run_fetch(self.connector, conn, self.concrete_ref(), ctx)

    def assert_no_secret(self, result: FetchResult, caplog=None) -> None:
        assert SECRET not in repr(result) and SECRET not in (result.detail or "")
        assert SECRET not in (result.filename or "")
        if caplog is not None:
            assert SECRET not in caplog.text

    # Declarations

    def test_declares_its_fields_correctly(self):
        c = self.connector
        assert isinstance(c.type, str) and c.type and c.type == c.type.lower()
        assert isinstance(c.display_name, str) and c.display_name
        names = [f.name for f in [*c.settings_fields, *c.ref_fields]]
        assert len(names) == len(set(names)), "field names must be unique"
        assert sum(f.secret for f in c.settings_fields) <= 1, "at most one secret field"
        assert not any(f.secret for f in c.ref_fields), "a release never pins a secret"

    def test_is_registered(self):
        if not self.registered:
            pytest.skip("not a registered connector")
        assert isinstance(REGISTRY.get(self.connector.type), type(self.connector))

    def test_settings_validate_and_normalize_idempotently(self):
        once = self.connector.validate_settings(self.settings)
        assert self.connector.validate_settings(once) == once
        assert not any(f.name in once for f in self.connector.settings_fields if f.secret)

    def test_unknown_settings_and_ref_keys_are_refused(self):
        with pytest.raises(SettingsError):
            self.connector.validate_settings({**self.settings, "unexpected_key": "x"})
        with pytest.raises(SettingsError):
            self.connector.validate_ref({**self.ref, "unexpected_key": "x"})

    def test_ref_validates_and_normalizes_idempotently(self):
        once = self.connector.validate_ref(self.ref)
        assert self.connector.validate_ref(once) == once

    def test_allowed_hosts_are_specific(self):
        hosts = self.connector.allowed_hosts(self.connector.validate_settings(self.settings))
        assert isinstance(hosts, list)
        for host in hosts:
            assert host and host not in ("*", "*.com", "*.net", "*.io"), f"{host!r} is too broad"


class FileConnectorContract(ConnectorContract):
    """For connectors that don't use the network (fixtures). Subclasses set ref to a file that
    exists and missing_ref to one that doesn't."""
    missing_ref: dict[str, Any] = {}

    def test_retrieves_the_file(self, caplog):
        result = self.fetch()
        assert result.outcome == RETRIEVED, result.detail
        if self.expected_filename:
            assert result.filename == self.expected_filename
        if self.expected_bytes is not None:
            assert result.data == self.expected_bytes

    def test_a_missing_file_is_not_found(self):
        saved, self.ref = self.ref, self.missing_ref
        try:
            assert self.fetch(direct=True).outcome == NOT_FOUND
        finally:
            self.ref = saved

    def test_uses_no_network(self):
        assert self.connector.allowed_hosts(self.connector.validate_settings(self.settings)) == []


class HttpConnectorContract(ConnectorContract):
    """For connectors that fetch over HTTP. Subclasses implement serve_file and serve_missing."""

    def serve_file(self, request: FakeRequest) -> FakeResponse:
        raise NotImplementedError

    def serve_missing(self, request: FakeRequest) -> FakeResponse:
        raise NotImplementedError

    def source(self, handler: Handler | None = None, dns: dict[str, list[str]] | None = None) -> FakeSource:
        return FakeSource(handler or self.serve_file, dns)

    def assert_contained(self, source: FakeSource) -> None:
        """Every request went to an allowed host at a public address, and never put the secret
        in a URL, where proxies and server logs would record it."""
        from safe_http import host_allowed
        allowed = self.connector.allowed_hosts(self.connector.validate_settings(self.settings))
        for request in source.requests:
            assert host_allowed(request.host, allowed), f"request to {request.host}, not an allowed host"
            assert request.address == PUBLIC_ADDRESS
            assert SECRET not in request.target, "the secret must not be in a URL"

    def check(self, handler: Handler, caplog, *, expected: tuple[str, ...] | None = None,
              dns: dict[str, list[str]] | None = None, **fetch_options) -> FetchResult:
        """fetch() on this source must return (not raise), stay contained and leak nothing; the
        run_fetch() result's outcome must be in expected."""
        caplog.set_level(logging.DEBUG)
        source = self.source(handler, dns)
        direct = self.fetch(source, direct=True, **fetch_options)
        assert isinstance(direct, FetchResult) and direct.outcome in OUTCOMES
        second = self.source(handler, dns)
        result = self.fetch(second, **fetch_options)
        self.assert_contained(source)
        self.assert_contained(second)
        self.assert_no_secret(direct, caplog)
        self.assert_no_secret(result, caplog)
        if expected is not None:
            assert result.outcome in expected, f"{result.outcome}: {result.detail}"
        if result.outcome != RETRIEVED:
            assert result.detail, "every failure needs a detail for source_failure_detail"
        return result

    # Success and absence

    def test_retrieves_the_file(self, caplog):
        source = self.source()
        result = self.fetch(source)
        assert result.outcome == RETRIEVED, result.detail
        if self.expected_filename:
            assert result.filename == self.expected_filename
        if self.expected_bytes is not None:
            assert result.data == self.expected_bytes
        self.assert_contained(source)
        if self.needs_secret:
            sent = " ".join(" ".join(r.headers.values()) for r in source.requests)
            assert SECRET in sent or any(SECRET in str(r.body) for r in source.requests) or "Basic" in sent, \
                "the connection's secret was never sent"

    def test_a_missing_file_is_not_found(self, caplog):
        self.check(self.serve_missing, caplog, expected=(NOT_FOUND,))

    # Credentials

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_are_auth_failed(self, caplog, status):
        self.check(lambda request: FakeResponse(status, b'{"message": "Unauthorized"}'), caplog,
                   expected=(AUTH_FAILED,))

    def test_a_connection_without_its_secret_is_auth_failed(self):
        if not self.needs_secret:
            pytest.skip("this connector has no secret")
        source = self.source()
        try:
            direct = self.fetch(source, store_secret=False, direct=True)
            assert direct.outcome != RETRIEVED
        except MissingSecret:
            pass
        assert self.fetch(self.source(), store_secret=False).outcome == AUTH_FAILED

    # The source misbehaving

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_server_errors_and_rate_limits_are_unavailable(self, caplog, status):
        self.check(lambda request: FakeResponse(status, b"<html>Service unavailable</html>"), caplog,
                   expected=(UNAVAILABLE,))

    @pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionRefusedError("refused")])
    def test_network_failures_are_unavailable(self, caplog, error):
        def fail(request):
            raise error
        self.check(fail, caplog, expected=(UNAVAILABLE,))

    def test_a_passed_deadline_is_unavailable_and_sends_nothing(self, caplog):
        source = self.source()
        result = self.fetch(source, deadline=time.monotonic() - 1)
        assert result.outcome == UNAVAILABLE
        assert source.requests == []

    def test_unparseable_answers_never_raise(self, caplog):
        for body in (b"", b"\x00\xff not json <", b"{}", b"[]", b'{"results": null}', b"null"):
            self.check(lambda request, body=body: FakeResponse(200, body), caplog)

    def test_an_oversized_answer_is_never_retrieved(self, caplog):
        def huge(request):
            response = self.serve_file(request)
            return FakeResponse(response.status, response.body + b" " * 4096, response.headers)
        result = self.check(huge, caplog, max_bytes=1024)
        assert result.outcome != RETRIEVED

    # Containment: redirects and addresses

    @pytest.mark.parametrize("location", [
        f"https://{METADATA_ADDRESS}/latest/meta-data/",
        "http://127.0.0.1:8000/bundles",
        "https://attacker.example/collect",
    ])
    def test_redirects_never_leave_the_allowed_hosts(self, caplog, location):
        result = self.check(lambda request: FakeResponse.redirect(location), caplog)
        assert result.outcome != RETRIEVED

    def test_a_host_resolving_to_an_internal_address_is_refused(self, caplog):
        hosts = self.connector.allowed_hosts(self.connector.validate_settings(self.settings))
        concrete = [h for h in hosts if not h.startswith("*.")]
        if not concrete:
            pytest.skip("no concrete host to point at an internal address")
        source = self.source(dns={host: [METADATA_ADDRESS] for host in concrete})
        result = self.fetch(source)
        assert result.outcome != RETRIEVED
        assert all(address != METADATA_ADDRESS for _, address in source.opened)
