"""Organizations, members, invites, connections, products and releases (Phase 5 plan, §2, step 1.3).

Every method that reads or writes an organization's data takes `org_id` as its first argument,
so no query can run without one. Rules about *who may* do something (roles, the last admin,
caps) live in the code that calls the store, not here. The store only keeps the data
consistent: things that must change together change in one transaction.

Two implementations share this interface and the same tests:
- InMemoryTenantStore, for tests and local runs without AWS;
- DynamoTenantStore, one DynamoDB table (§2):

    PK                  SK                            item
    ORG#<org_id>        META                          organization
    ORG#<org_id>        MEMBER#<sub>                  membership
    ORG#<org_id>        INVITE#<email>                invite
    ORG#<org_id>        CONN#<conn_id>                connection
    ORG#<org_id>        PRODUCT#<product_id>          product (rules for each category)
    ORG#<org_id>        RELEASE#<product_id>#<rel>    release (pins for each category)
    USER#<sub>          ORG#<org_id>                  reverse lookup: a person's organizations
    USER#<sub>          CREATED#<org_id>              organizations a person created (for the cap)
    INVITE#<email>      ORG#<org_id>                  reverse lookup: invites at login
"""
import copy
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Protocol

ROLES = ("admin", "member")
CONNECTION_STATUSES = ("active", "disconnected")

# Product and release IDs come from the pod's requests (e.g. "prd-88", "v1.2-github-inaccessible").
# "#" separates key parts, and "/" or ".." could be mistaken for paths, so neither is allowed.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class TenantStoreError(Exception):
    """Base class for store errors."""


class NotFound(TenantStoreError):
    pass


class AlreadyExists(TenantStoreError):
    pass


@dataclass(frozen=True)
class Organization:
    org_id: str
    name: str
    created_by: str
    created_at: int


@dataclass(frozen=True)
class Membership:
    org_id: str
    sub: str
    role: str
    joined_at: int


@dataclass(frozen=True)
class Invite:
    org_id: str
    email: str
    role: str
    invited_by: str
    expires_at: int


@dataclass(frozen=True)
class Connection:
    org_id: str
    conn_id: str
    type: str
    settings: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    # Where SecretStore keeps this connection's token, if it has one
    secret_path: str | None = None
    created_at: int = 0


@dataclass(frozen=True)
class Product:
    org_id: str
    product_id: str
    # category -> {"conn": conn_id, ...rule for finding a release's file}
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Release:
    org_id: str
    product_id: str
    release: str
    # category -> {"conn": conn_id, ...pinned reference}
    pins: dict[str, dict[str, Any]] = field(default_factory=dict)


def normalize_email(email: str) -> str:
    if not isinstance(email, str) or "@" not in email or any(c.isspace() for c in email.strip()):
        raise ValueError("email must be an email address")
    return email.strip().lower()


def check_uuid(value: str, what: str) -> str:
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError(f"{what} must be a lower-case UUID")


def check_name(value: str, what: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value) or ".." in value:
        raise ValueError(f"{what} must be 1-128 letters, digits, '.', '_' or '-', and not contain '..'")
    return value


def check_sub(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or "#" in value or not value.isprintable():
        raise ValueError("sub must be a Cognito user ID")
    return value


def check_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    return role


class TenantStore(Protocol):
    # Organizations
    def create_org(self, name: str, created_by: str) -> Organization: ...
    def get_org(self, org_id: str) -> Organization | None: ...
    def rename_org(self, org_id: str, name: str) -> Organization: ...
    def delete_org(self, org_id: str) -> None: ...
    def count_orgs_created_by(self, sub: str) -> int: ...

    # Members
    def get_member(self, org_id: str, sub: str) -> Membership | None: ...
    def list_members(self, org_id: str) -> list[Membership]: ...
    def add_member(self, org_id: str, sub: str, role: str) -> Membership: ...
    def set_role(self, org_id: str, sub: str, role: str) -> Membership: ...
    def remove_member(self, org_id: str, sub: str) -> None: ...
    def list_user_orgs(self, sub: str) -> list[Membership]: ...

    # Invites
    def create_invite(self, org_id: str, email: str, role: str, invited_by: str, expires_at: int) -> Invite: ...
    def list_invites(self, org_id: str) -> list[Invite]: ...
    def invites_for_email(self, email: str) -> list[Invite]: ...
    def delete_invite(self, org_id: str, email: str) -> None: ...
    def accept_invite(self, org_id: str, email: str, sub: str) -> Membership: ...

    # Connections
    def create_connection(self, org_id: str, type: str, settings: dict[str, Any],
                          secret_path: str | None = None) -> Connection: ...
    def get_connection(self, org_id: str, conn_id: str) -> Connection | None: ...
    def list_connections(self, org_id: str) -> list[Connection]: ...
    def update_connection(self, org_id: str, conn_id: str, *, settings: dict[str, Any] | None = None,
                          status: str | None = None, secret_path: str | None = None) -> Connection: ...
    def delete_connection(self, org_id: str, conn_id: str) -> None: ...

    # Products and releases
    def put_product(self, org_id: str, product_id: str, rules: dict[str, dict[str, Any]]) -> Product: ...
    def get_product(self, org_id: str, product_id: str) -> Product | None: ...
    def list_products(self, org_id: str) -> list[Product]: ...
    def delete_product(self, org_id: str, product_id: str) -> None: ...
    def put_release(self, org_id: str, product_id: str, release: str,
                    pins: dict[str, dict[str, Any]]) -> Release: ...
    def get_release(self, org_id: str, product_id: str, release: str) -> Release | None: ...
    def list_releases(self, org_id: str, product_id: str) -> list[Release]: ...
    def delete_release(self, org_id: str, product_id: str, release: str) -> None: ...


def _now() -> int:
    return int(time.time())


# In memory

class InMemoryTenantStore:
    """Same behavior as DynamoTenantStore, kept in dicts. For tests and local runs without AWS."""

    def __init__(self, clock: Callable[[], int] = _now):
        self._clock = clock
        self._lock = threading.RLock()
        self._orgs: dict[str, Organization] = {}
        self._members: dict[tuple[str, str], Membership] = {}
        self._invites: dict[tuple[str, str], Invite] = {}
        self._connections: dict[tuple[str, str], Connection] = {}
        self._products: dict[tuple[str, str], Product] = {}
        self._releases: dict[tuple[str, str, str], Release] = {}

    def _require_org(self, org_id: str) -> None:
        check_uuid(org_id, "org_id")
        if org_id not in self._orgs:
            raise NotFound("organization not found")

    # Organizations

    def create_org(self, name: str, created_by: str) -> Organization:
        name = _check_org_name(name)
        check_sub(created_by)
        with self._lock:
            org = Organization(str(uuid.uuid4()), name, created_by, self._clock())
            self._orgs[org.org_id] = org
            self._members[(org.org_id, created_by)] = Membership(org.org_id, created_by, "admin", org.created_at)
            return org

    def get_org(self, org_id: str) -> Organization | None:
        check_uuid(org_id, "org_id")
        with self._lock:
            return self._orgs.get(org_id)

    def rename_org(self, org_id: str, name: str) -> Organization:
        name = _check_org_name(name)
        with self._lock:
            self._require_org(org_id)
            org = replace(self._orgs[org_id], name=name)
            self._orgs[org_id] = org
            return org

    def delete_org(self, org_id: str) -> None:
        check_uuid(org_id, "org_id")
        with self._lock:
            self._orgs.pop(org_id, None)
            tables: tuple[dict[Any, Any], ...] = (self._members, self._invites, self._connections,
                                                  self._products, self._releases)
            for table in tables:
                for key in [k for k in table if k[0] == org_id]:
                    del table[key]

    def count_orgs_created_by(self, sub: str) -> int:
        check_sub(sub)
        with self._lock:
            return sum(org.created_by == sub for org in self._orgs.values())

    # Members

    def get_member(self, org_id: str, sub: str) -> Membership | None:
        check_uuid(org_id, "org_id")
        check_sub(sub)
        with self._lock:
            return self._members.get((org_id, sub))

    def list_members(self, org_id: str) -> list[Membership]:
        check_uuid(org_id, "org_id")
        with self._lock:
            return sorted((m for (o, _), m in self._members.items() if o == org_id), key=lambda m: m.sub)

    def add_member(self, org_id: str, sub: str, role: str) -> Membership:
        check_sub(sub)
        check_role(role)
        with self._lock:
            self._require_org(org_id)
            if (org_id, sub) in self._members:
                raise AlreadyExists("already a member")
            member = Membership(org_id, sub, role, self._clock())
            self._members[(org_id, sub)] = member
            return member

    def set_role(self, org_id: str, sub: str, role: str) -> Membership:
        check_role(role)
        with self._lock:
            member = self.get_member(org_id, sub)
            if member is None:
                raise NotFound("member not found")
            member = replace(member, role=role)
            self._members[(org_id, sub)] = member
            return member

    def remove_member(self, org_id: str, sub: str) -> None:
        check_uuid(org_id, "org_id")
        check_sub(sub)
        with self._lock:
            self._members.pop((org_id, sub), None)

    def list_user_orgs(self, sub: str) -> list[Membership]:
        check_sub(sub)
        with self._lock:
            return sorted((m for (_, s), m in self._members.items() if s == sub), key=lambda m: m.org_id)

    # Invites

    def create_invite(self, org_id: str, email: str, role: str, invited_by: str, expires_at: int) -> Invite:
        email = normalize_email(email)
        check_role(role)
        check_sub(invited_by)
        with self._lock:
            self._require_org(org_id)
            invite = Invite(org_id, email, role, invited_by, int(expires_at))
            self._invites[(org_id, email)] = invite
            return invite

    def list_invites(self, org_id: str) -> list[Invite]:
        check_uuid(org_id, "org_id")
        now = self._clock()
        with self._lock:
            return sorted((i for (o, _), i in self._invites.items() if o == org_id and i.expires_at > now),
                          key=lambda i: i.email)

    def invites_for_email(self, email: str) -> list[Invite]:
        email = normalize_email(email)
        now = self._clock()
        with self._lock:
            return sorted((i for (_, e), i in self._invites.items() if e == email and i.expires_at > now),
                          key=lambda i: i.org_id)

    def delete_invite(self, org_id: str, email: str) -> None:
        check_uuid(org_id, "org_id")
        with self._lock:
            self._invites.pop((org_id, normalize_email(email)), None)

    def accept_invite(self, org_id: str, email: str, sub: str) -> Membership:
        email = normalize_email(email)
        check_sub(sub)
        with self._lock:
            self._require_org(org_id)
            invite = self._invites.get((org_id, email))
            if invite is None or invite.expires_at <= self._clock():
                raise NotFound("invite not found or expired")
            if (org_id, sub) in self._members:
                raise AlreadyExists("already a member")
            member = Membership(org_id, sub, invite.role, self._clock())
            self._members[(org_id, sub)] = member
            del self._invites[(org_id, email)]
            return member

    # Connections

    def create_connection(self, org_id: str, type: str, settings: dict[str, Any],
                          secret_path: str | None = None) -> Connection:
        _check_type(type)
        with self._lock:
            self._require_org(org_id)
            conn = Connection(org_id, str(uuid.uuid4()), type, copy.deepcopy(settings), "active",
                              secret_path, self._clock())
            self._connections[(org_id, conn.conn_id)] = conn
            return copy.deepcopy(conn)

    def get_connection(self, org_id: str, conn_id: str) -> Connection | None:
        check_uuid(org_id, "org_id")
        check_uuid(conn_id, "conn_id")
        with self._lock:
            return copy.deepcopy(self._connections.get((org_id, conn_id)))

    def list_connections(self, org_id: str) -> list[Connection]:
        check_uuid(org_id, "org_id")
        with self._lock:
            return copy.deepcopy(sorted((c for (o, _), c in self._connections.items() if o == org_id),
                                        key=lambda c: c.conn_id))

    def update_connection(self, org_id: str, conn_id: str, *, settings: dict[str, Any] | None = None,
                          status: str | None = None, secret_path: str | None = None) -> Connection:
        if status is not None and status not in CONNECTION_STATUSES:
            raise ValueError(f"status must be one of {CONNECTION_STATUSES}")
        with self._lock:
            conn = self.get_connection(org_id, conn_id)
            if conn is None:
                raise NotFound("connection not found")
            changes: dict[str, Any] = {}
            if settings is not None:
                changes["settings"] = copy.deepcopy(settings)
            if status is not None:
                changes["status"] = status
            if secret_path is not None:
                changes["secret_path"] = secret_path
            conn = replace(conn, **changes)
            self._connections[(org_id, conn_id)] = conn
            return copy.deepcopy(conn)

    def delete_connection(self, org_id: str, conn_id: str) -> None:
        check_uuid(org_id, "org_id")
        check_uuid(conn_id, "conn_id")
        with self._lock:
            self._connections.pop((org_id, conn_id), None)

    # Products and releases

    def put_product(self, org_id: str, product_id: str, rules: dict[str, dict[str, Any]]) -> Product:
        check_name(product_id, "product_id")
        with self._lock:
            self._require_org(org_id)
            product = Product(org_id, product_id, copy.deepcopy(rules))
            self._products[(org_id, product_id)] = product
            return copy.deepcopy(product)

    def get_product(self, org_id: str, product_id: str) -> Product | None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        with self._lock:
            return copy.deepcopy(self._products.get((org_id, product_id)))

    def list_products(self, org_id: str) -> list[Product]:
        check_uuid(org_id, "org_id")
        with self._lock:
            return copy.deepcopy(sorted((p for (o, _), p in self._products.items() if o == org_id),
                                        key=lambda p: p.product_id))

    def delete_product(self, org_id: str, product_id: str) -> None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        with self._lock:
            self._products.pop((org_id, product_id), None)
            for key in [k for k in self._releases if k[:2] == (org_id, product_id)]:
                del self._releases[key]

    def put_release(self, org_id: str, product_id: str, release: str,
                    pins: dict[str, dict[str, Any]]) -> Release:
        check_name(product_id, "product_id")
        check_name(release, "release")
        with self._lock:
            self._require_org(org_id)
            item = Release(org_id, product_id, release, copy.deepcopy(pins))
            self._releases[(org_id, product_id, release)] = item
            return copy.deepcopy(item)

    def get_release(self, org_id: str, product_id: str, release: str) -> Release | None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        check_name(release, "release")
        with self._lock:
            return copy.deepcopy(self._releases.get((org_id, product_id, release)))

    def list_releases(self, org_id: str, product_id: str) -> list[Release]:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        with self._lock:
            return copy.deepcopy(sorted((r for k, r in self._releases.items() if k[:2] == (org_id, product_id)),
                                        key=lambda r: r.release))

    def delete_release(self, org_id: str, product_id: str, release: str) -> None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        check_name(release, "release")
        with self._lock:
            self._releases.pop((org_id, product_id, release), None)


def _check_org_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100 or not name.isprintable():
        raise ValueError("organization name must be 1-100 printable characters")
    return name.strip()


def _check_type(type: str) -> str:
    if not isinstance(type, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", type):
        raise ValueError("connection type must be a short lower-case name")
    return type


# DynamoDB

def _plain(value: Any) -> Any:
    """DynamoDB returns numbers as Decimal; give back ints (or floats) as they were written."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _org_pk(org_id: str) -> str:
    return "ORG#" + org_id


class DynamoTenantStore:
    """TenantStore on one DynamoDB table with string keys PK and SK (§2).

    Expired invites are filtered out on read; the `ttl` attribute also lets DynamoDB's
    time-to-live remove them later, once step 1.4 turns TTL on for the table.
    """

    def __init__(self, table_name: str, client: Any = None, region: str | None = None,
                 clock: Callable[[], int] = _now):
        if client is None:
            import boto3
            client = boto3.client("dynamodb", region_name=region)
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
        self.table = table_name
        self._client = client
        self._clock = clock
        self._ser = TypeSerializer()
        self._de = TypeDeserializer()

    # Low-level helpers

    def _item(self, values: dict[str, Any]) -> dict[str, Any]:
        return {k: self._ser.serialize(v) for k, v in values.items() if v is not None}

    def _key(self, pk: str, sk: str) -> dict[str, Any]:
        return {"PK": {"S": pk}, "SK": {"S": sk}}

    def _read(self, item: dict[str, Any]) -> dict[str, Any]:
        return _plain({k: self._de.deserialize(v) for k, v in item.items()})

    def _get(self, pk: str, sk: str) -> dict[str, Any] | None:
        response = self._client.get_item(TableName=self.table, Key=self._key(pk, sk), ConsistentRead=True)
        return self._read(response["Item"]) if "Item" in response else None

    def _query(self, pk: str, prefix: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"TableName": self.table, "ConsistentRead": True,
                                  "KeyConditionExpression": "PK = :pk",
                                  "ExpressionAttributeValues": {":pk": {"S": pk}}}
        if prefix:   # DynamoDB refuses begins_with on an empty string
            kwargs["KeyConditionExpression"] += " AND begins_with(SK, :prefix)"
            kwargs["ExpressionAttributeValues"][":prefix"] = {"S": prefix}
        while True:
            response = self._client.query(**kwargs)
            items.extend(self._read(item) for item in response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def _put(self, values: dict[str, Any], condition: str | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {"TableName": self.table, "Item": self._item(values)}
        if condition:
            entry["ConditionExpression"] = condition
        return entry

    def _org_exists(self, org_id: str) -> dict[str, Any]:
        return {"ConditionCheck": {"TableName": self.table, "Key": self._key(_org_pk(org_id), "META"),
                                   "ConditionExpression": "attribute_exists(PK)"}}

    def _transact(self, items: list[dict[str, Any]], failure: TenantStoreError) -> None:
        try:
            self._client.transact_write_items(TransactItems=items)
        except self._client.exceptions.TransactionCanceledException:
            raise failure from None

    def _require_org(self, org_id: str) -> None:
        check_uuid(org_id, "org_id")
        if self._get(_org_pk(org_id), "META") is None:
            raise NotFound("organization not found")

    # Organizations

    def create_org(self, name: str, created_by: str) -> Organization:
        name = _check_org_name(name)
        check_sub(created_by)
        org = Organization(str(uuid.uuid4()), name, created_by, self._clock())
        pk = _org_pk(org.org_id)
        member = {"role": "admin", "joined_at": org.created_at}
        self._transact([
            {"Put": self._put({"PK": pk, "SK": "META", "kind": "org", "name": name, "created_by": created_by,
                               "created_at": org.created_at}, "attribute_not_exists(PK)")},
            {"Put": self._put({"PK": pk, "SK": "MEMBER#" + created_by, "kind": "member", **member})},
            {"Put": self._put({"PK": "USER#" + created_by, "SK": pk, "kind": "user_org", **member})},
            {"Put": self._put({"PK": "USER#" + created_by, "SK": "CREATED#" + org.org_id, "kind": "created"})},
        ], AlreadyExists("organization already exists"))
        return org

    def get_org(self, org_id: str) -> Organization | None:
        check_uuid(org_id, "org_id")
        item = self._get(_org_pk(org_id), "META")
        return None if item is None else Organization(org_id, item["name"], item["created_by"], item["created_at"])

    def rename_org(self, org_id: str, name: str) -> Organization:
        name = _check_org_name(name)
        check_uuid(org_id, "org_id")
        try:
            response = self._client.update_item(
                TableName=self.table, Key=self._key(_org_pk(org_id), "META"),
                UpdateExpression="SET #n = :name", ConditionExpression="attribute_exists(PK)",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={":name": {"S": name}}, ReturnValues="ALL_NEW")
        except self._client.exceptions.ConditionalCheckFailedException:
            raise NotFound("organization not found") from None
        item = self._read(response["Attributes"])
        return Organization(org_id, item["name"], item["created_by"], item["created_at"])

    def delete_org(self, org_id: str) -> None:
        check_uuid(org_id, "org_id")
        pk = _org_pk(org_id)
        keys = []
        for item in self._query(pk, ""):
            keys.append((pk, item["SK"]))
            if item["SK"] == "META":
                keys.append(("USER#" + item["created_by"], "CREATED#" + org_id))
            elif item["SK"].startswith("MEMBER#"):
                keys.append(("USER#" + item["SK"][len("MEMBER#"):], pk))
            elif item["SK"].startswith("INVITE#"):
                keys.append((item["SK"], pk))
        # META goes last, so an interrupted delete still finds the organization and can be run again
        keys.sort(key=lambda key: key == (pk, "META"))
        for start in range(0, len(keys), 25):
            requests = [{"DeleteRequest": {"Key": self._key(*key)}} for key in keys[start:start + 25]]
            pending: dict[str, Any] = {self.table: requests}
            while pending:
                response = self._client.batch_write_item(RequestItems=pending)
                pending = response.get("UnprocessedItems") or {}

    def count_orgs_created_by(self, sub: str) -> int:
        check_sub(sub)
        # Counts organizations the person created even if they've since left them, so leaving
        # doesn't get around the cap
        return len(self._query("USER#" + sub, "CREATED#"))

    # Members

    def get_member(self, org_id: str, sub: str) -> Membership | None:
        check_uuid(org_id, "org_id")
        check_sub(sub)
        item = self._get(_org_pk(org_id), "MEMBER#" + sub)
        return None if item is None else Membership(org_id, sub, item["role"], item["joined_at"])

    def list_members(self, org_id: str) -> list[Membership]:
        check_uuid(org_id, "org_id")
        return [Membership(org_id, item["SK"][len("MEMBER#"):], item["role"], item["joined_at"])
                for item in self._query(_org_pk(org_id), "MEMBER#")]

    def _member_writes(self, org_id: str, sub: str, role: str, joined_at: int) -> list[dict[str, Any]]:
        pk = _org_pk(org_id)
        values = {"role": role, "joined_at": joined_at}
        return [
            {"Put": self._put({"PK": pk, "SK": "MEMBER#" + sub, "kind": "member", **values},
                              "attribute_not_exists(PK)")},
            {"Put": self._put({"PK": "USER#" + sub, "SK": pk, "kind": "user_org", **values})},
        ]

    def add_member(self, org_id: str, sub: str, role: str) -> Membership:
        check_uuid(org_id, "org_id")
        check_sub(sub)
        check_role(role)
        self._require_org(org_id)
        member = Membership(org_id, sub, role, self._clock())
        self._transact([self._org_exists(org_id), *self._member_writes(org_id, sub, role, member.joined_at)],
                       AlreadyExists("already a member"))
        return member

    def set_role(self, org_id: str, sub: str, role: str) -> Membership:
        check_role(role)
        member = self.get_member(org_id, sub)
        if member is None:
            raise NotFound("member not found")
        pk = _org_pk(org_id)
        update = {"UpdateExpression": "SET #r = :role", "ExpressionAttributeNames": {"#r": "role"},
                  "ExpressionAttributeValues": {":role": {"S": role}}}
        self._transact([
            {"Update": {"TableName": self.table, "Key": self._key(pk, "MEMBER#" + sub),
                        "ConditionExpression": "attribute_exists(PK)", **update}},
            {"Update": {"TableName": self.table, "Key": self._key("USER#" + sub, pk), **update}},
        ], NotFound("member not found"))
        return replace(member, role=role)

    def remove_member(self, org_id: str, sub: str) -> None:
        check_uuid(org_id, "org_id")
        check_sub(sub)
        pk = _org_pk(org_id)
        self._transact([
            {"Delete": {"TableName": self.table, "Key": self._key(pk, "MEMBER#" + sub)}},
            {"Delete": {"TableName": self.table, "Key": self._key("USER#" + sub, pk)}},
        ], NotFound("member not found"))

    def list_user_orgs(self, sub: str) -> list[Membership]:
        check_sub(sub)
        return [Membership(item["SK"][len("ORG#"):], sub, item["role"], item["joined_at"])
                for item in self._query("USER#" + sub, "ORG#")]

    # Invites

    def create_invite(self, org_id: str, email: str, role: str, invited_by: str, expires_at: int) -> Invite:
        check_uuid(org_id, "org_id")
        email = normalize_email(email)
        check_role(role)
        check_sub(invited_by)
        invite = Invite(org_id, email, role, invited_by, int(expires_at))
        pk = _org_pk(org_id)
        values = {"role": role, "invited_by": invited_by, "expires_at": invite.expires_at,
                  "ttl": invite.expires_at}
        self._transact([
            self._org_exists(org_id),
            {"Put": self._put({"PK": pk, "SK": "INVITE#" + email, "kind": "invite", **values})},
            {"Put": self._put({"PK": "INVITE#" + email, "SK": pk, "kind": "email_invite", **values})},
        ], NotFound("organization not found"))
        return invite

    def list_invites(self, org_id: str) -> list[Invite]:
        check_uuid(org_id, "org_id")
        now = self._clock()
        return [Invite(org_id, item["SK"][len("INVITE#"):], item["role"], item["invited_by"], item["expires_at"])
                for item in self._query(_org_pk(org_id), "INVITE#") if item["expires_at"] > now]

    def invites_for_email(self, email: str) -> list[Invite]:
        email = normalize_email(email)
        now = self._clock()
        return [Invite(item["SK"][len("ORG#"):], email, item["role"], item["invited_by"], item["expires_at"])
                for item in self._query("INVITE#" + email, "ORG#") if item["expires_at"] > now]

    def delete_invite(self, org_id: str, email: str) -> None:
        check_uuid(org_id, "org_id")
        email = normalize_email(email)
        pk = _org_pk(org_id)
        self._transact([
            {"Delete": {"TableName": self.table, "Key": self._key(pk, "INVITE#" + email)}},
            {"Delete": {"TableName": self.table, "Key": self._key("INVITE#" + email, pk)}},
        ], NotFound("invite not found"))

    def accept_invite(self, org_id: str, email: str, sub: str) -> Membership:
        check_uuid(org_id, "org_id")
        email = normalize_email(email)
        check_sub(sub)
        pk = _org_pk(org_id)
        item = self._get(pk, "INVITE#" + email)
        now = self._clock()
        if item is None or item["expires_at"] <= now:
            raise NotFound("invite not found or expired")
        if self.get_member(org_id, sub) is not None:
            raise AlreadyExists("already a member")
        member = Membership(org_id, sub, item["role"], now)
        self._transact([
            self._org_exists(org_id),
            # The invite must still be there and unexpired, so it can't be accepted twice
            {"Delete": {"TableName": self.table, "Key": self._key(pk, "INVITE#" + email),
                        "ConditionExpression": "attribute_exists(PK) AND expires_at > :now",
                        "ExpressionAttributeValues": {":now": {"N": str(now)}}}},
            {"Delete": {"TableName": self.table, "Key": self._key("INVITE#" + email, pk)}},
            *self._member_writes(org_id, sub, item["role"], now),
        ], NotFound("invite not found, expired or already used"))
        return member

    # Connections

    def _connection(self, org_id: str, item: dict[str, Any]) -> Connection:
        return Connection(org_id, item["SK"][len("CONN#"):], item["type"], item.get("settings", {}),
                          item["status"], item.get("secret_path"), item["created_at"])

    def create_connection(self, org_id: str, type: str, settings: dict[str, Any],
                          secret_path: str | None = None) -> Connection:
        check_uuid(org_id, "org_id")
        _check_type(type)
        conn = Connection(org_id, str(uuid.uuid4()), type, copy.deepcopy(settings), "active", secret_path,
                          self._clock())
        self._transact([
            self._org_exists(org_id),
            {"Put": self._put({"PK": _org_pk(org_id), "SK": "CONN#" + conn.conn_id, "kind": "connection",
                               "type": type, "settings": settings, "status": conn.status,
                               "secret_path": secret_path, "created_at": conn.created_at},
                              "attribute_not_exists(PK)")},
        ], NotFound("organization not found"))
        return conn

    def get_connection(self, org_id: str, conn_id: str) -> Connection | None:
        check_uuid(org_id, "org_id")
        check_uuid(conn_id, "conn_id")
        item = self._get(_org_pk(org_id), "CONN#" + conn_id)
        return None if item is None else self._connection(org_id, item)

    def list_connections(self, org_id: str) -> list[Connection]:
        check_uuid(org_id, "org_id")
        return [self._connection(org_id, item) for item in self._query(_org_pk(org_id), "CONN#")]

    def update_connection(self, org_id: str, conn_id: str, *, settings: dict[str, Any] | None = None,
                          status: str | None = None, secret_path: str | None = None) -> Connection:
        check_uuid(org_id, "org_id")
        check_uuid(conn_id, "conn_id")
        if status is not None and status not in CONNECTION_STATUSES:
            raise ValueError(f"status must be one of {CONNECTION_STATUSES}")
        changes = {"settings": settings, "status": status, "secret_path": secret_path}
        changes = {k: v for k, v in changes.items() if v is not None}
        if not changes:
            conn = self.get_connection(org_id, conn_id)
            if conn is None:
                raise NotFound("connection not found")
            return conn
        names = {f"#{k}": k for k in changes}
        values = {f":{k}": self._ser.serialize(v) for k, v in changes.items()}
        try:
            response = self._client.update_item(
                TableName=self.table, Key=self._key(_org_pk(org_id), "CONN#" + conn_id),
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in changes),
                ConditionExpression="attribute_exists(PK)", ExpressionAttributeNames=names,
                ExpressionAttributeValues=values, ReturnValues="ALL_NEW")
        except self._client.exceptions.ConditionalCheckFailedException:
            raise NotFound("connection not found") from None
        return self._connection(org_id, self._read(response["Attributes"]))

    def delete_connection(self, org_id: str, conn_id: str) -> None:
        check_uuid(org_id, "org_id")
        check_uuid(conn_id, "conn_id")
        self._client.delete_item(TableName=self.table, Key=self._key(_org_pk(org_id), "CONN#" + conn_id))

    # Products and releases

    def put_product(self, org_id: str, product_id: str, rules: dict[str, dict[str, Any]]) -> Product:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        self._transact([
            self._org_exists(org_id),
            {"Put": self._put({"PK": _org_pk(org_id), "SK": "PRODUCT#" + product_id, "kind": "product",
                               "rules": rules})},
        ], NotFound("organization not found"))
        return Product(org_id, product_id, copy.deepcopy(rules))

    def get_product(self, org_id: str, product_id: str) -> Product | None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        item = self._get(_org_pk(org_id), "PRODUCT#" + product_id)
        return None if item is None else Product(org_id, product_id, item.get("rules", {}))

    def list_products(self, org_id: str) -> list[Product]:
        check_uuid(org_id, "org_id")
        return [Product(org_id, item["SK"][len("PRODUCT#"):], item.get("rules", {}))
                for item in self._query(_org_pk(org_id), "PRODUCT#")]

    def delete_product(self, org_id: str, product_id: str) -> None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        for release in self.list_releases(org_id, product_id):
            self.delete_release(org_id, product_id, release.release)
        self._client.delete_item(TableName=self.table, Key=self._key(_org_pk(org_id), "PRODUCT#" + product_id))

    def put_release(self, org_id: str, product_id: str, release: str,
                    pins: dict[str, dict[str, Any]]) -> Release:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        check_name(release, "release")
        self._transact([
            self._org_exists(org_id),
            {"Put": self._put({"PK": _org_pk(org_id), "SK": f"RELEASE#{product_id}#{release}",
                               "kind": "release", "pins": pins})},
        ], NotFound("organization not found"))
        return Release(org_id, product_id, release, copy.deepcopy(pins))

    def get_release(self, org_id: str, product_id: str, release: str) -> Release | None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        check_name(release, "release")
        item = self._get(_org_pk(org_id), f"RELEASE#{product_id}#{release}")
        return None if item is None else Release(org_id, product_id, release, item.get("pins", {}))

    def list_releases(self, org_id: str, product_id: str) -> list[Release]:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        prefix = f"RELEASE#{product_id}#"
        return [Release(org_id, product_id, item["SK"][len(prefix):], item.get("pins", {}))
                for item in self._query(_org_pk(org_id), prefix)]

    def delete_release(self, org_id: str, product_id: str, release: str) -> None:
        check_uuid(org_id, "org_id")
        check_name(product_id, "product_id")
        check_name(release, "release")
        self._client.delete_item(TableName=self.table,
                                 Key=self._key(_org_pk(org_id), f"RELEASE#{product_id}#{release}"))
