"""The only way connectors make network requests (Phase 5 plan, step 1.2).

Companies type in their own Jenkins or Confluence addresses, and the server then fetches from
them. Without a guard, an address such as http://169.254.169.254/ (the EC2 metadata service,
which hands out the server's AWS credentials) or http://127.0.0.1:8000/ (gunicorn) would make
the server attack itself. Every request here:

- must be HTTPS, to a host name the connection allows (exact, or "*.example.com");
- has its host resolved once, and is refused if *any* address is not a public unicast
  address (loopback, private, link-local, shared, reserved, multicast, and the IPv4 forms
  hidden in IPv4-mapped and NAT64 IPv6 addresses);
- then connects to the address that was checked, with the TLS certificate verified against the
  host name, so DNS can't switch to another address between the check and the connection;
- never follows redirects: a 3xx comes back as a response, and the connector decides;
- stops at a timeout, at the caller's deadline, at `max_bytes` for one response, and at a
  `ByteBudget` shared across a whole bundle.

`private_networks` lets *operators* allow specific internal networks (for example our own
test Jenkins). It comes from server configuration only, never from a company's settings.
"""
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 3.0
DEFAULT_MAX_BYTES = 1024 * 1024
USER_AGENT = "cra-documentation-service"
_CHUNK = 64 * 1024
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class SafeHttpError(Exception):
    """A request that was refused or failed before a complete response arrived.

    `code` is one of: bad_url, host_not_allowed, address_not_allowed, dns, connection, tls,
    timeout, too_large. The message is safe to show: it never holds headers, tokens or
    response bodies.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Response:
    status: int
    # Lower-case names; repeated headers are joined with ", "
    headers: Mapping[str, str]
    body: bytes = field(repr=False)


class ByteBudget:
    """Bytes a whole bundle may download, shared by every request made for it."""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def consume(self, amount: int) -> None:
        with self._lock:
            if self.used + amount > self.limit:
                raise SafeHttpError("too_large", "The bundle's download limit was reached.")
            self.used += amount


def host_allowed(host: str, patterns: Iterable[str]) -> bool:
    """Exact names, or "*.example.com", which matches subdomains but not example.com itself."""
    for pattern in patterns:
        pattern = pattern.lower().rstrip(".")
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and len(host) > len(pattern) - 1:
                return True
        elif host == pattern:
            return True
    return False


def address_allowed(address: IPAddress, private_networks: Iterable[IPNetwork] = ()) -> bool:
    if any(address in network for network in private_networks):
        return True
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return address_allowed(address.ipv4_mapped, private_networks)
        if address in _NAT64:
            return address_allowed(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF), private_networks)
    return address.is_global and not address.is_multicast


Resolver = Callable[[str, int], list[str]]


def resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to one already-checked address, with SNI and certificate checks for the host name."""

    def __init__(self, host: str, address: str, port: int, timeout: float, context: ssl.SSLContext):
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._tls = context

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


ConnectionFactory = Callable[[str, str, int, float], http.client.HTTPConnection]


class SafeHttpClient:
    def __init__(self, allowed_hosts: Iterable[str] | None, *, timeout: float = DEFAULT_TIMEOUT,
                 max_bytes: int = DEFAULT_MAX_BYTES, budget: ByteBudget | None = None,
                 deadline: float | None = None, allowed_ports: Iterable[int] = (443,),
                 private_networks: Iterable[IPNetwork] = (), resolver: Resolver = resolve,
                 connection_factory: ConnectionFactory | None = None,
                 clock: Callable[[], float] = time.monotonic):
        """allowed_hosts=None allows any host name; addresses are checked either way.
        deadline is a value of `clock` after which no request may continue."""
        self.allowed_hosts = None if allowed_hosts is None else tuple(allowed_hosts)
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.budget = budget
        self.deadline = deadline
        self.allowed_ports = frozenset(allowed_ports)
        self.private_networks = tuple(private_networks)
        self._resolver = resolver
        self._clock = clock
        self._connection_factory = connection_factory or self._default_connection
        self._tls = ssl.create_default_context()

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> Response:
        return self.request("GET", url, headers=headers)

    def request(self, method: str, url: str, headers: Mapping[str, str] | None = None,
                body: bytes | None = None) -> Response:
        host, port, target = self._check_url(url)
        address = self._check_addresses(host, port)
        connection = self._connection_factory(host, address, port, self._time_left())
        try:
            sent = {"User-Agent": USER_AGENT, **(headers or {})}
            connection.request(method, target, body=body, headers=sent)
            response = connection.getresponse()
            data = self._read(response)
            joined: dict[str, str] = {}
            for name, value in response.getheaders():
                name = name.lower()
                joined[name] = f"{joined[name]}, {value}" if name in joined else value
            return Response(response.status, joined, data)
        except SafeHttpError:
            raise
        except ssl.SSLError:
            raise SafeHttpError("tls", f"The TLS connection to {host} could not be verified.") from None
        except (TimeoutError, socket.timeout):
            raise SafeHttpError("timeout", f"{host} did not answer in time.") from None
        except (OSError, http.client.HTTPException):
            raise SafeHttpError("connection", f"Could not complete the request to {host}.") from None
        finally:
            connection.close()

    def _check_url(self, url: str) -> tuple[str, int, str]:
        if not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
            raise SafeHttpError("bad_url", "The address is not a valid URL.")
        try:
            parts = urlsplit(url)
            port = parts.port or 443
        except ValueError:
            raise SafeHttpError("bad_url", "The address is not a valid URL.") from None
        if parts.scheme != "https":
            raise SafeHttpError("bad_url", "Only https:// addresses are allowed.")
        if parts.username is not None or parts.password is not None:
            raise SafeHttpError("bad_url", "Addresses must not contain a user name or password.")
        host = (parts.hostname or "").rstrip(".")
        if not host or not host.isascii():
            raise SafeHttpError("bad_url", "The address has no valid host name.")
        if port not in self.allowed_ports:
            raise SafeHttpError("bad_url", f"Port {port} is not allowed.")
        if self.allowed_hosts is not None and not host_allowed(host, self.allowed_hosts):
            raise SafeHttpError("host_not_allowed", f"{host} is not an allowed host for this connection.")
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        return host, port, target

    def _check_addresses(self, host: str, port: int) -> str:
        self._time_left()
        try:
            addresses = [ipaddress.ip_address(a.split("%")[0]) for a in self._resolver(host, port)]
        except (OSError, ValueError):
            raise SafeHttpError("dns", f"{host} could not be resolved.") from None
        if not addresses:
            raise SafeHttpError("dns", f"{host} could not be resolved.")
        # Refuse if any address is internal, so a name that mixes public and internal ones can't
        # be used to reach the internal one
        if not all(address_allowed(a, self.private_networks) for a in addresses):
            raise SafeHttpError("address_not_allowed", f"{host} resolves to an address that isn't allowed.")
        return str(addresses[0])

    def _read(self, response: http.client.HTTPResponse) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            self._time_left()
            chunk = response.read(min(_CHUNK, self.max_bytes + 1 - total))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.max_bytes:
                raise SafeHttpError("too_large", f"The response is larger than {self.max_bytes} bytes.")
            if self.budget is not None:
                self.budget.consume(len(chunk))
            chunks.append(chunk)

    def _time_left(self) -> float:
        if self.deadline is None:
            return self.timeout
        left = self.deadline - self._clock()
        if left <= 0:
            raise SafeHttpError("timeout", "The bundle ran out of time.")
        return min(self.timeout, left)

    def _default_connection(self, host: str, address: str, port: int, timeout: float) -> http.client.HTTPConnection:
        return _PinnedHTTPSConnection(host, address, port, timeout, self._tls)
