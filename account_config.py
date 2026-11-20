"""The pod-test account: connections, products and releases from test_account.json (kit step 2.2).

The pod's bundle API has no logins, so every POST /bundles is served from this one account. It
holds the shared cross-team releases whose files come from connectors (Jenkins, GitHub,
Confluence, or our fixtures folder). Releases it doesn't define keep using fixtures/ and
source_catalog.json, exactly as before.

    {
      "org_id": "<uuid>",
      "name": "pod-test",
      "connections": {"<conn uuid>": {"type": "jenkins", "settings": {...}}},
      "products": {
        "prd-88": {
          "rules":    {"<category>": {"conn": "<conn uuid>", ...ref, placeholders allowed}},
          "releases": {"v1.5-jenkins": {"<category>": {"conn": "<conn uuid>", ...ref}}}
        }
      }
    }

A release's pin for a category wins over its product's rule. Tokens are never in this file:
each connection's token lives in the secret store under the connection's id.

The whole file is checked when it's loaded, with the same validation a company's connections get,
so a mistake stops the service at start-up instead of failing one bundle later.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from connectors import REGISTRY
from connectors.base import SettingsError
from tenant_store import Connection, Product, Release, check_name, check_uuid

ACCOUNT_PATH = Path(__file__).parent / "test_account.json"
ACCOUNT_ENV = "CRA_TEST_ACCOUNT"
CATEGORIES = ("SBOM", "Risk Assessment", "Test Logs", "User Instructions")


class AccountConfigError(ValueError):
    """test_account.json is malformed. The message says where."""


@dataclass(frozen=True)
class AccountConfig:
    org_id: str
    name: str
    connections: dict[str, Connection] = field(default_factory=dict)
    products: dict[str, Product] = field(default_factory=dict)
    releases: dict[tuple[str, str], Release] = field(default_factory=dict)

    # The same reads as TenantStore, so the pipeline can take a company's account the same way later

    def get_connection(self, org_id: str, conn_id: str) -> Connection | None:
        return self.connections.get(conn_id) if org_id == self.org_id else None

    def get_product(self, org_id: str, product_id: str) -> Product | None:
        return self.products.get(product_id) if org_id == self.org_id else None

    def get_release(self, org_id: str, product_id: str, release: str) -> Release | None:
        return self.releases.get((product_id, release)) if org_id == self.org_id else None


EMPTY_ACCOUNT = AccountConfig(org_id="00000000-0000-4000-8000-000000000000", name="none")


def load_account(path: Path | str | None = None) -> AccountConfig:
    """The account in path (default: CRA_TEST_ACCOUNT, else test_account.json). A missing file
    gives an empty account; a malformed one raises AccountConfigError."""
    if path is None:
        path = os.environ.get(ACCOUNT_ENV) or ACCOUNT_PATH
    path = Path(path)
    if not path.is_file():
        return EMPTY_ACCOUNT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AccountConfigError(f"{path.name}: not readable JSON ({error})") from None
    return parse_account(data, where=path.name)


def parse_account(data: Any, where: str = "account") -> AccountConfig:
    obj = _object(data, where)
    try:
        org_id = check_uuid(str(obj.get("org_id")), "org_id")
    except ValueError as error:
        raise AccountConfigError(f"{where}: {error}") from None
    name = obj.get("name")
    if not isinstance(name, str) or not name:
        raise AccountConfigError(f"{where}: name must be a non-empty string")

    connections: dict[str, Connection] = {}
    for conn_id, spec in _object(obj.get("connections", {}), f"{where}: connections").items():
        here = f"{where}: connection {conn_id}"
        try:
            check_uuid(conn_id, "connection id")
        except ValueError as error:
            raise AccountConfigError(f"{here}: {error}") from None
        spec = _object(spec, here)
        connector = REGISTRY.get(str(spec.get("type")))
        if connector is None:
            raise AccountConfigError(f"{here}: unknown type {spec.get('type')!r}; "
                                     f"registered: {', '.join(sorted(REGISTRY))}")
        try:
            settings = connector.validate_settings(spec.get("settings") or {})
        except SettingsError as error:
            raise AccountConfigError(f"{here}: settings {error}") from None
        connections[conn_id] = Connection(org_id, conn_id, connector.type, settings)

    products: dict[str, Product] = {}
    releases: dict[tuple[str, str], Release] = {}
    for product_id, spec in _object(obj.get("products", {}), f"{where}: products").items():
        here = f"{where}: product {product_id}"
        _name(product_id, "product_id", here)
        spec = _object(spec, here)
        rules = _entries(spec.get("rules", {}), connections, f"{here}: rules")
        products[product_id] = Product(org_id, product_id, rules)
        for release, pins in _object(spec.get("releases", {}), f"{here}: releases").items():
            _name(release, "release", here)
            releases[(product_id, release)] = Release(
                org_id, product_id, release, _entries(pins, connections, f"{here}: release {release}"))
    return AccountConfig(org_id, name, connections, products, releases)


def _entries(data: Any, connections: dict[str, Connection], where: str) -> dict[str, dict[str, Any]]:
    """{category: {"conn": id, ...ref}}, each ref validated by its connection's connector."""
    clean: dict[str, dict[str, Any]] = {}
    for category, entry in _object(data, where).items():
        here = f"{where}: {category}"
        if category not in CATEGORIES:
            raise AccountConfigError(f"{here}: not a category (one of {', '.join(CATEGORIES)})")
        entry = dict(_object(entry, here))
        conn = connections.get(str(entry.pop("conn", "")))
        if conn is None:
            raise AccountConfigError(f"{here}: conn must be the id of a connection in this account")
        try:
            ref = REGISTRY[conn.type].validate_ref(entry)
        except SettingsError as error:
            raise AccountConfigError(f"{here}: {error}") from None
        clean[category] = {"conn": conn.conn_id, **ref}
    return clean


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AccountConfigError(f"{where}: must be a JSON object")
    return value


def _name(value: str, what: str, where: str) -> None:
    try:
        check_name(value, what)
    except ValueError as error:
        raise AccountConfigError(f"{where}: {error}") from None
