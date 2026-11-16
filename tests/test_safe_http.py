"""The outbound request guard every connector uses (Phase 5 plan, step 1.2)."""
import ipaddress
import socket
import threading

import pytest

from safe_http import ByteBudget, SafeHttpClient, SafeHttpError, address_allowed, host_allowed

PUBLIC = "93.184.215.14"


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=(("Content-Type", "text/plain"),)):
        self.status = status
        self._body = body
        self._headers = list(headers)

    def read(self, amount):
        chunk, self._body = self._body[:amount], self._body[amount:]
        return chunk

    def getheaders(self):
        return self._headers


class FakeConnection:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.sent = None
        self.closed = False

    def request(self, method, target, body=None, headers=None):
        if self.error:
            raise self.error
        self.sent = (method, target, body, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def client(addresses=(PUBLIC,), connection=None, **options):
    opened = []
    connection = connection or FakeConnection()

    def factory(host, address, port, timeout):
        opened.append((host, address, port, timeout))
        return connection

    options.setdefault("allowed_hosts", ["example.com", "*.atlassian.net"])
    made = SafeHttpClient(resolver=lambda host, port: list(addresses), connection_factory=factory, **options)
    return made, opened, connection


def refused(made, url, code):
    with pytest.raises(SafeHttpError) as error:
        made.get(url)
    assert error.value.code == code
    return error.value


# URLs

@pytest.mark.parametrize("url", [
    "http://example.com/",                    # not HTTPS
    "ftp://example.com/",
    "https://user:pass@example.com/",         # credentials in the URL
    "https://user@example.com/",
    "https:///no-host",
    "https://example.com:8443/",              # port not allowed
    "https://example.com:notaport/",
    "https://exa mple.com/",
    "https://example.com/\npath",
    "https://exämple.com/",
    "//example.com/",
])
def test_bad_urls_are_refused_before_any_lookup(url):
    made, opened, _ = client()
    refused(made, url, "bad_url")
    assert opened == []


@pytest.mark.parametrize("host, allowed", [
    ("example.com", True),
    ("EXAMPLE.com.", True),
    ("team.atlassian.net", True),
    ("a.b.atlassian.net", True),
    ("atlassian.net", False),                 # the wildcard covers subdomains only
    ("evilatlassian.net", False),
    ("atlassian.net.evil.com", False),
    ("example.com.evil.com", False),
])
def test_only_allowed_host_names_are_reached(host, allowed):
    made, opened, _ = client()
    if allowed:
        assert made.get(f"https://{host}/x").status == 200
        assert opened[0][0] == host.lower().rstrip(".")
    else:
        refused(made, f"https://{host}/x", "host_not_allowed")
        assert opened == []


def test_any_host_name_is_allowed_when_the_connection_names_none():
    made, opened, _ = client(allowed_hosts=None)
    assert made.get("https://jenkins.customer.example/job/x").status == 200


# Addresses

@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1",   # loopback and private
    "169.254.169.254",                                       # EC2 instance metadata
    "100.64.0.1",                                            # shared (carrier-grade NAT)
    "0.0.0.0", "255.255.255.255", "224.0.0.1",               # unspecified, broadcast, multicast
    "::1", "fd00:ec2::254", "fe80::1",                       # IPv6 loopback, EC2 metadata, link-local
    "::ffff:127.0.0.1", "::ffff:169.254.169.254",            # IPv4-mapped
    "64:ff9b::a9fe:a9fe",                                    # NAT64 form of 169.254.169.254
    "ff02::1",
])
def test_internal_addresses_are_refused(address):
    made, opened, _ = client(addresses=[address])
    refused(made, "https://example.com/", "address_not_allowed")
    assert opened == []


def test_a_name_resolving_to_public_and_internal_addresses_is_refused():
    made, opened, _ = client(addresses=[PUBLIC, "169.254.169.254"])
    refused(made, "https://example.com/", "address_not_allowed")
    assert opened == []


def test_the_connection_goes_to_the_checked_address_not_a_new_lookup():
    lookups = []

    def resolver(host, port):
        lookups.append(host)
        # A second lookup would get an internal address (DNS rebinding)
        return [PUBLIC] if len(lookups) == 1 else ["127.0.0.1"]

    opened = []
    made = SafeHttpClient(["example.com"], resolver=resolver,
                          connection_factory=lambda h, a, p, t: opened.append((h, a)) or FakeConnection())
    made.get("https://example.com/")
    assert lookups == ["example.com"]
    assert opened == [("example.com", PUBLIC)]


def test_operators_can_allow_a_private_network():
    network = ipaddress.ip_network("10.0.5.0/24")
    made, opened, _ = client(addresses=["10.0.5.9"], private_networks=[network])
    assert made.get("https://example.com/").status == 200
    made, _, _ = client(addresses=["10.0.6.9"], private_networks=[network])
    refused(made, "https://example.com/", "address_not_allowed")


def test_a_failed_lookup_is_a_dns_error():
    def resolver(host, port):
        raise socket.gaierror("no such host")

    made = SafeHttpClient(["example.com"], resolver=resolver, connection_factory=lambda *a: FakeConnection())
    refused(made, "https://example.com/", "dns")


def test_address_allowed_accepts_public_addresses():
    for address in ("8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8"):
        assert address_allowed(ipaddress.ip_address(address))


# Responses

def test_a_response_comes_back_with_status_headers_and_bytes():
    connection = FakeConnection(FakeResponse(200, b"\x00raw bytes\xff",
                                             [("Content-Type", "text/plain"), ("Set-Cookie", "a"),
                                              ("set-cookie", "b")]))
    made, opened, _ = client(connection=connection)
    response = made.get("https://example.com/wiki/api?version=3", headers={"Authorization": "Bearer t"})
    assert response.status == 200
    assert response.body == b"\x00raw bytes\xff"
    assert response.headers == {"content-type": "text/plain", "set-cookie": "a, b"}
    method, target, _, headers = connection.sent
    assert (method, target) == ("GET", "/wiki/api?version=3")
    assert headers["Authorization"] == "Bearer t" and headers["User-Agent"]
    assert connection.closed


def test_a_redirect_is_returned_not_followed():
    connection = FakeConnection(FakeResponse(302, b"", [("Location", "https://169.254.169.254/")]))
    made, opened, _ = client(connection=connection)
    response = made.get("https://example.com/")
    assert response.status == 302
    assert len(opened) == 1


def test_a_response_over_the_size_limit_is_refused():
    made, _, connection = client(connection=FakeConnection(FakeResponse(body=b"x" * 101)), max_bytes=100)
    refused(made, "https://example.com/", "too_large")
    assert connection.closed
    made, _, _ = client(connection=FakeConnection(FakeResponse(body=b"x" * 100)), max_bytes=100)
    assert len(made.get("https://example.com/").body) == 100


def test_the_bundle_budget_is_shared_across_requests():
    budget = ByteBudget(150)
    for _ in range(2):
        made, _, _ = client(connection=FakeConnection(FakeResponse(body=b"x" * 70)), budget=budget)
        made.get("https://example.com/")
    made, _, _ = client(connection=FakeConnection(FakeResponse(body=b"x" * 70)), budget=budget)
    refused(made, "https://example.com/", "too_large")


def test_no_request_starts_after_the_deadline():
    now = [100.0]
    made, opened, _ = client(deadline=100.0, clock=lambda: now[0])
    refused(made, "https://example.com/", "timeout")
    assert opened == []


def test_the_timeout_shrinks_to_the_time_left_before_the_deadline():
    now = [99.0]
    made, opened, _ = client(deadline=100.0, timeout=3.0, clock=lambda: now[0])
    made.get("https://example.com/")
    assert opened[0][3] == pytest.approx(1.0)


@pytest.mark.parametrize("error, code", [
    (socket.timeout("slow"), "timeout"),
    (ConnectionRefusedError(), "connection"),
])
def test_transport_errors_become_safe_errors(error, code):
    made, _, connection = client(connection=FakeConnection(error=error))
    message = str(refused(made, "https://example.com/", code))
    assert "example.com" in message
    assert connection.closed


def test_error_messages_never_contain_the_request_headers():
    made, _, _ = client(connection=FakeConnection(error=ConnectionResetError("Bearer secret-token")))
    with pytest.raises(SafeHttpError) as error:
        made.get("https://example.com/", headers={"Authorization": "Bearer secret-token"})
    assert "secret-token" not in str(error.value)


def test_host_allowed_patterns():
    assert host_allowed("a.example.com", ["*.example.com"])
    assert not host_allowed("example.com", ["*.example.com"])


# The real connection, without leaving this machine

def test_the_real_connection_dials_the_checked_address_and_verifies_tls():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    accepted = []

    def serve():
        conn, _ = server.accept()
        accepted.append(conn.recv(5))   # the start of a TLS ClientHello
        conn.sendall(b"not tls\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    made = SafeHttpClient(["sut.example"], allowed_ports=[port], resolver=lambda h, p: ["127.0.0.1"],
                          private_networks=[ipaddress.ip_network("127.0.0.0/8")], timeout=2)
    with pytest.raises(SafeHttpError) as error:
        made.get(f"https://sut.example:{port}/")
    thread.join()
    server.close()
    assert error.value.code in ("tls", "connection")
    assert accepted and accepted[0][:1] == b"\x16"   # a TLS handshake record reached the checked address


def test_localhost_is_refused_with_the_real_resolver():
    made = SafeHttpClient(None)
    with pytest.raises(SafeHttpError) as error:
        made.get("https://localhost/")
    assert error.value.code == "address_not_allowed"
