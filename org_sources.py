"""A company's connections, products and releases (Phase 5 plan, §5 and §6, step 5.3).

- A **connection** is one source (a GitHub repository, a Confluence site, a Jenkins server) with
  its settings and token. Admins add, edit and delete them. The token goes straight to the secret
  store and is never shown again; the CONN item keeps only where it is (`secret_path`).
- Editing a connection so its requests go to different hosts requires entering the token again,
  so a stored token can't be pointed at another server.
- A GitHub token must be fine-grained (`github_pat_`), and one read of the repository checks it
  on save, so a typo fails on the form and not in a bundle.
- A **product** maps categories to a connection and a rule (placeholders {product} and {release}
  allowed). A **release** pins categories explicitly; a pin wins over the product's rule.
- A connection that a product or release uses can't be deleted, so no category ever points at
  a connection that's gone.

Only admins change any of this; every member can read it. Every check re-reads the MEMBER record
through Accounts, and every read and write is scoped to the organization.
"""
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from accounts import AccountError, Accounts
from account_config import CATEGORIES
from connectors import REGISTRY
from connectors.base import Connector, SettingsError
from safe_http import SafeHttpClient, SafeHttpError
from secret_store import SecretStore
from tenant_store import Connection, Product, Release, check_name

MAX_CONNECTIONS = 10
MAX_PRODUCTS = 50
MAX_RELEASES = 200
MAX_SECRET_LENGTH = 1024
# Connection types a company may add. "fixtures" reads our own folder, so only pod-test has it
COMPANY_TYPES = tuple(t for t in ("github", "confluence", "jenkins") if t in REGISTRY)

# (connector type, settings, token) -> None if the token works, else a message for the form
TokenCheck = Callable[[str, Mapping[str, Any], str], "str | None"]


def check_token(conn_type: str, settings: Mapping[str, Any], token: str) -> str | None:
    """The checks a token gets before it's stored. GitHub: fine-grained only, and it must be able
    to read the repository. The others are checked by their first bundle."""
    if conn_type != "github":
        return None
    if not token.startswith("github_pat_"):
        return ("Use a fine-grained personal access token (it starts with github_pat_) with read-only "
                "Contents access to this one repository. Classic tokens can't be limited to one repository.")
    repository = settings["repository"]
    try:
        response = SafeHttpClient(["api.github.com"], max_bytes=256 * 1024).get(
            f"https://api.github.com/repos/{repository}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"})
    except SafeHttpError:
        return "GitHub couldn't be reached to check the token. Try again in a minute."
    if response.status == 200:
        return None
    if response.status == 401:
        return "GitHub didn't accept that token. Check it hasn't expired, or create a new one."
    if response.status in (403, 404):
        if response.headers.get("x-ratelimit-remaining") == "0":
            return "GitHub is rate limiting us. Try again in a few minutes."
        return f"That token can't read {repository}. Give it read-only Contents access to that repository."
    return f"GitHub couldn't check the token (HTTP {response.status}). Try again in a minute."


def _connector(conn_type: str) -> Connector:
    if conn_type not in COMPANY_TYPES:
        raise AccountError("That isn't a connection type you can add.")
    return REGISTRY[conn_type]


def _secret_field(connector: Connector):
    return next((f for f in connector.settings_fields if f.secret), None)


def _clean_secret(value: Any) -> str:
    token = value.strip() if isinstance(value, str) else ""
    if len(token) > MAX_SECRET_LENGTH or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise AccountError("That token isn't valid: it's too long or contains spaces.")
    return token


def _settings_error(error: SettingsError) -> AccountError:
    return AccountError(f"Check the form: {error}.")


@dataclass(frozen=True)
class Usage:
    """Where a connection is used: products whose rules use it, and (product, release) pins."""
    products: tuple[str, ...]
    releases: tuple[tuple[str, str], ...]

    def __bool__(self) -> bool:
        return bool(self.products or self.releases)

    def describe(self) -> str:
        names = [f"product {p}" for p in self.products] + [f"release {p} {r}" for p, r in self.releases]
        shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
        return shown


class OrgSources:
    def __init__(self, accounts: Accounts, secrets: SecretStore, token_check: TokenCheck = check_token):
        self.accounts = accounts
        self.store = accounts.store
        self.secrets = secrets
        self._check = token_check
        self._lock = accounts._lock

    # Connections

    def connections(self, org_id: str, actor: str) -> list[Connection]:
        self.accounts.require(org_id, actor)
        return sorted(self.store.list_connections(org_id), key=lambda c: (c.type, c.created_at, c.conn_id))

    def connection(self, org_id: str, actor: str, conn_id: str) -> Connection:
        self.accounts.require(org_id, actor)
        conn = self._find(org_id, conn_id)
        if conn is None:
            raise AccountError("That connection doesn't exist.", 404)
        return conn

    def _find(self, org_id: str, conn_id: Any) -> Connection | None:
        try:
            return self.store.get_connection(org_id, conn_id)
        except ValueError:
            return None

    def add_connection(self, org_id: str, actor: str, conn_type: str, form: Mapping[str, Any]) -> Connection:
        connector = _connector(conn_type)
        secret_field = _secret_field(connector)
        try:
            settings = connector.validate_settings({f.name: form.get(f.name, "") for f in connector.settings_fields
                                                    if not f.secret})
        except SettingsError as error:
            raise _settings_error(error) from None
        token = _clean_secret(form.get(secret_field.name, "")) if secret_field else ""
        if secret_field and not token:
            raise AccountError(f"Enter the {secret_field.label.lower()}.")
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if len(self.store.list_connections(org_id)) >= MAX_CONNECTIONS:
                raise AccountError(f"An organization can have at most {MAX_CONNECTIONS} connections.", 403)
        if secret_field and (problem := self._check(conn_type, settings, token)):
            raise AccountError(problem)
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            conn = self.store.create_connection(org_id, conn_type, settings)
            if not secret_field:
                return conn
            try:
                path = self.secrets.put_connection_secret(org_id, conn.conn_id, token)
                return self.store.update_connection(org_id, conn.conn_id, secret_path=path)
            except Exception:
                # Never leave a connection whose token wasn't stored
                self.store.delete_connection(org_id, conn.conn_id)
                try:
                    self.secrets.delete_connection_secret(org_id, conn.conn_id)
                except Exception:  # nosec B110  # best effort: the parameter may never have been written
                    pass
                raise AccountError("The token couldn't be stored. Try again.", 503) from None

    def edit_connection(self, org_id: str, actor: str, conn_id: str, form: Mapping[str, Any]) -> Connection:
        """New settings, and optionally a new token. A blank token keeps the stored one, unless the
        new settings send requests to different hosts."""
        conn = self.connection(org_id, actor, conn_id)
        connector = REGISTRY[conn.type]
        secret_field = _secret_field(connector)
        try:
            settings = connector.validate_settings({f.name: form.get(f.name, "") for f in connector.settings_fields
                                                    if not f.secret})
        except SettingsError as error:
            raise _settings_error(error) from None
        token = _clean_secret(form.get(secret_field.name, "")) if secret_field else ""
        hosts_changed = sorted(connector.allowed_hosts(settings)) != sorted(connector.allowed_hosts(conn.settings))
        if secret_field and not token and hosts_changed:
            raise AccountError(f"These settings send requests to a different server, so enter the "
                               f"{secret_field.label.lower()} again.")
        if secret_field and token and (problem := self._check(conn.type, settings, token)):
            raise AccountError(problem)
        if secret_field and not token and conn.type == "github" and settings != conn.settings:
            # The stored token was checked against the old repository; check it against the new one
            try:
                stored = self.secrets.get_connection_secret(org_id, conn_id).reveal()
            except Exception:
                raise AccountError("Enter the token again.") from None
            if problem := self._check(conn.type, settings, stored):
                raise AccountError(problem)
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if self._find(org_id, conn_id) is None:
                raise AccountError("That connection doesn't exist.", 404)
            if token:
                try:
                    path = self.secrets.put_connection_secret(org_id, conn_id, token)
                except Exception:
                    raise AccountError("The token couldn't be stored. Try again.", 503) from None
                return self.store.update_connection(org_id, conn_id, settings=settings, secret_path=path,
                                                    status="active")
            return self.store.update_connection(org_id, conn_id, settings=settings)

    def usage(self, org_id: str, conn_id: str) -> Usage:
        products, releases = [], []
        for product in self.store.list_products(org_id):
            if any(entry.get("conn") == conn_id for entry in product.rules.values()):
                products.append(product.product_id)
            for release in self.store.list_releases(org_id, product.product_id):
                if any(entry.get("conn") == conn_id for entry in release.pins.values()):
                    releases.append((product.product_id, release.release))
        return Usage(tuple(products), tuple(releases))

    def delete_connection(self, org_id: str, actor: str, conn_id: str) -> None:
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if self._find(org_id, conn_id) is None:
                raise AccountError("That connection doesn't exist.", 404)
            if used := self.usage(org_id, conn_id):
                raise AccountError(f"That connection is still used by {used.describe()}. "
                                   "Change those first.", 409)
            try:
                self.secrets.delete_connection_secret(org_id, conn_id)
            except Exception:
                raise AccountError("The token couldn't be deleted. Try again.", 503) from None
            self.store.delete_connection(org_id, conn_id)

    # Products and releases

    def products(self, org_id: str, actor: str) -> list[Product]:
        self.accounts.require(org_id, actor)
        return sorted(self.store.list_products(org_id), key=lambda p: p.product_id.lower())

    def product(self, org_id: str, actor: str, product_id: str) -> Product:
        self.accounts.require(org_id, actor)
        found = self._product(org_id, product_id)
        if found is None:
            raise AccountError("That product doesn't exist.", 404)
        return found

    def _product(self, org_id: str, product_id: Any) -> Product | None:
        try:
            return self.store.get_product(org_id, product_id)
        except ValueError:
            return None

    def releases(self, org_id: str, actor: str, product_id: str) -> list[Release]:
        self.product(org_id, actor, product_id)
        return sorted(self.store.list_releases(org_id, product_id), key=lambda r: r.release.lower())

    def release(self, org_id: str, actor: str, product_id: str, release: str) -> Release:
        self.product(org_id, actor, product_id)
        try:
            found = self.store.get_release(org_id, product_id, release)
        except ValueError:
            found = None
        if found is None:
            raise AccountError("That release doesn't exist.", 404)
        return found

    def create_product(self, org_id: str, actor: str, product_id: str) -> Product:
        product_id = _name(product_id, "A product ID")
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if self._product(org_id, product_id) is not None:
                raise AccountError("That product already exists.", 409)
            if len(self.store.list_products(org_id)) >= MAX_PRODUCTS:
                raise AccountError(f"An organization can have at most {MAX_PRODUCTS} products.", 403)
            return self.store.put_product(org_id, product_id, {})

    def delete_product(self, org_id: str, actor: str, product_id: str) -> None:
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if self._product(org_id, product_id) is None:
                raise AccountError("That product doesn't exist.", 404)
            self.store.delete_product(org_id, product_id)

    def create_release(self, org_id: str, actor: str, product_id: str, release: str) -> Release:
        release = _name(release, "A release")
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if self._product(org_id, product_id) is None:
                raise AccountError("That product doesn't exist.", 404)
            existing = self.store.list_releases(org_id, product_id)
            if any(r.release == release for r in existing):
                raise AccountError("That release already exists.", 409)
            if len(existing) >= MAX_RELEASES:
                raise AccountError(f"A product can have at most {MAX_RELEASES} pinned releases.", 403)
            return self.store.put_release(org_id, product_id, release, {})

    def delete_release(self, org_id: str, actor: str, product_id: str, release: str) -> None:
        with self._lock:
            self.release(org_id, actor, product_id, release)
            self.accounts.require(org_id, actor, admin=True)
            self.store.delete_release(org_id, product_id, release)

    def _entry(self, org_id: str, conn_id: Any, form: Mapping[str, Any]) -> dict[str, Any]:
        conn = self._find(org_id, conn_id)
        if conn is None:
            raise AccountError("Choose one of this organization's connections.")
        connector = REGISTRY[conn.type]
        try:
            ref = connector.validate_ref({f.name: form.get(f.name, "") for f in connector.ref_fields})
        except SettingsError as error:
            raise _settings_error(error) from None
        return {"conn": conn.conn_id, **ref}

    def set_entry(self, org_id: str, actor: str, product_id: str, release: str | None, category: str,
                  conn_id: Any, form: Mapping[str, Any]) -> None:
        """Set a product's rule (release None) or a release's pin for one category."""
        if category not in CATEGORIES:
            raise AccountError("That isn't a category.")
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            entry = self._entry(org_id, conn_id, form)
            if release is None:
                product = self.product(org_id, actor, product_id)
                self.store.put_product(org_id, product_id, {**product.rules, category: entry})
            else:
                current = self.release(org_id, actor, product_id, release)
                self.store.put_release(org_id, product_id, release, {**current.pins, category: entry})

    def clear_entry(self, org_id: str, actor: str, product_id: str, release: str | None, category: str) -> None:
        with self._lock:
            self.accounts.require(org_id, actor, admin=True)
            if release is None:
                product = self.product(org_id, actor, product_id)
                rules = {k: v for k, v in product.rules.items() if k != category}
                self.store.put_product(org_id, product_id, rules)
            else:
                current = self.release(org_id, actor, product_id, release)
                pins = {k: v for k, v in current.pins.items() if k != category}
                self.store.put_release(org_id, product_id, release, pins)


def _name(value: Any, what: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    try:
        return check_name(value, "name")
    except ValueError:
        raise AccountError(f"{what} is 1 to 128 letters, digits, '.', '_' or '-' (e.g. prd-88 or v1.2).") from None


def describe_ref(entry: Mapping[str, Any]) -> str:
    """A short, readable summary of a rule or pin, without the connection id."""
    parts = []
    for key, value in entry.items():
        if key == "conn":
            continue
        shown = ", ".join(value) if isinstance(value, list) else str(value)
        parts.append(f"{key}: {shown}")
    return "; ".join(parts)


def ref_form_values(entry: Mapping[str, Any] | None) -> dict[str, str]:
    """An existing entry as form values (lists one per line)."""
    values = {}
    for key, value in (entry or {}).items():
        if key != "conn":
            values[key] = "\n".join(value) if isinstance(value, list) else str(value)
    return values


def entry_conn(entry: Mapping[str, Any] | None) -> str | None:
    return entry.get("conn") if entry else None

