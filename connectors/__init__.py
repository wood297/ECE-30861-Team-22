"""Connector registry (Phase 5 plan, §11.2).

To add a connector, import it and add one line to REGISTRY. The connection pages, the
test_account.json check and the pipeline all read connector types from here.
"""
import time
from typing import Any, Callable, Iterable, Mapping

from connectors.base import Connector, FetchContext
from connectors.fixtures import FixturesConnector
from connectors.confluence import ConfluenceConnector
from safe_http import DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT, ByteBudget, IPNetwork, SafeHttpClient
from secret_store import SecretStore
from tenant_store import Connection

REGISTRY: dict[str, Connector] = {
    "fixtures": FixturesConnector(),
    # "github": GitHubConnector(),
    "confluence": ConfluenceConnector(),
    # "jenkins": JenkinsConnector(),
}


def make_context(connector: Connector, conn: Connection, secrets: SecretStore | None, *,
                 deadline: float | None = None, budget: ByteBudget | None = None,
                 private_networks: Iterable[IPNetwork] = (), timeout: float = DEFAULT_TIMEOUT,
                 max_bytes: int = DEFAULT_MAX_BYTES, clock: Callable[[], float] = time.monotonic,
                 http_options: Mapping[str, Any] | None = None) -> FetchContext:
    """The FetchContext for one fetch from one connection.

    private_networks comes from server configuration only (safe_http). http_options passes
    extra SafeHttpClient arguments, which tests use for a fake resolver and connections."""
    http = SafeHttpClient(connector.allowed_hosts(conn.settings), timeout=timeout, max_bytes=max_bytes,
                          budget=budget, deadline=deadline, private_networks=private_networks, clock=clock,
                          **dict(http_options or {}))
    loader = None
    if secrets is not None and any(f.secret for f in connector.settings_fields):
        store = secrets

        def loader():
            return store.get_connection_secret(conn.org_id, conn.conn_id)
    return FetchContext(http, loader, deadline=deadline, clock=clock,
                        log_prefix=f"[{connector.type} {conn.conn_id[:8]}] ")
