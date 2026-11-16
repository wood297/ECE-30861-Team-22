"""TenantStore contract (Phase 5 plan, step 1.3), run against both implementations.

The DynamoDB version runs on moto, which imitates AWS in this process, so these tests need no
AWS access and never reach AWS.
"""
import uuid

import boto3
import pytest
from moto import mock_aws

from tenant_store import AlreadyExists, DynamoTenantStore, InMemoryTenantStore, NotFound

TABLE = "team22-cra-tenants-test"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def aws_env(monkeypatch):
    # Fake credentials, so nothing here could use a real AWS login even if moto were bypassed
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield


def make_table(client):
    client.create_table(TableName=TABLE, BillingMode="PAY_PER_REQUEST",
                        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                                              {"AttributeName": "SK", "AttributeType": "S"}],
                        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"},
                                   {"AttributeName": "SK", "KeyType": "RANGE"}])


@pytest.fixture(params=["memory", "dynamodb"])
def store(request, clock):
    if request.param == "memory":
        yield InMemoryTenantStore(clock=clock)
        return
    request.getfixturevalue("aws_env")
    client = boto3.client("dynamodb", region_name="us-east-2")
    make_table(client)
    yield DynamoTenantStore(TABLE, client=client, clock=clock)


ALICE, BOB, CAROL = "sub-alice", "sub-bob", "sub-carol"


# Organizations

def test_creating_an_organization_makes_the_creator_its_admin(store, clock):
    org = store.create_org("  Acme Corp ", ALICE)
    assert org.name == "Acme Corp" and org.created_by == ALICE and org.created_at == clock.now
    assert store.get_org(org.org_id) == org
    member = store.get_member(org.org_id, ALICE)
    assert member is not None and member.role == "admin"
    assert [m.org_id for m in store.list_user_orgs(ALICE)] == [org.org_id]


def test_an_unknown_organization_is_none_or_not_found(store):
    missing = str(uuid.uuid4())
    assert store.get_org(missing) is None
    with pytest.raises(NotFound):
        store.rename_org(missing, "x")
    with pytest.raises(NotFound):
        store.add_member(missing, BOB, "member")
    with pytest.raises(NotFound):
        store.create_connection(missing, "github", {})
    with pytest.raises(NotFound):
        store.put_product(missing, "prd-88", {})


def test_renaming_an_organization(store):
    org = store.create_org("Acme", ALICE)
    assert store.rename_org(org.org_id, "Acme Inc").name == "Acme Inc"
    assert store.get_org(org.org_id).name == "Acme Inc"


def test_the_created_count_includes_organizations_the_creator_left(store):
    first = store.create_org("One", ALICE)
    store.create_org("Two", ALICE)
    store.create_org("Bob's", BOB)
    store.add_member(first.org_id, BOB, "admin")
    store.remove_member(first.org_id, ALICE)
    assert store.count_orgs_created_by(ALICE) == 2
    assert store.count_orgs_created_by(BOB) == 1
    store.delete_org(first.org_id)
    assert store.count_orgs_created_by(ALICE) == 1


def test_deleting_an_organization_removes_everything_that_belongs_to_it(store, clock):
    org = store.create_org("Acme", ALICE)
    other = store.create_org("Other", BOB)
    store.add_member(org.org_id, BOB, "member")
    store.create_invite(org.org_id, "carol@example.com", "member", ALICE, clock.now + 100)
    conn = store.create_connection(org.org_id, "github", {"repository": "acme/app"})
    store.put_product(org.org_id, "app", {"SBOM": {"conn": conn.conn_id}})
    store.put_release(org.org_id, "app", "v1", {"SBOM": {"conn": conn.conn_id, "ref": "abc"}})

    store.delete_org(org.org_id)

    assert store.get_org(org.org_id) is None
    assert store.list_members(org.org_id) == []
    assert store.list_invites(org.org_id) == []
    assert store.invites_for_email("carol@example.com") == []
    assert store.list_connections(org.org_id) == []
    assert store.list_products(org.org_id) == []
    assert store.list_releases(org.org_id, "app") == []
    assert store.list_user_orgs(ALICE) == []
    assert [m.org_id for m in store.list_user_orgs(BOB)] == [other.org_id]
    assert store.get_org(other.org_id) is not None


# Members

def test_adding_listing_changing_and_removing_members(store):
    org = store.create_org("Acme", ALICE)
    store.add_member(org.org_id, BOB, "member")
    assert [(m.sub, m.role) for m in store.list_members(org.org_id)] == [(ALICE, "admin"), (BOB, "member")]
    assert store.set_role(org.org_id, BOB, "admin").role == "admin"
    assert store.get_member(org.org_id, BOB).role == "admin"
    assert store.list_user_orgs(BOB)[0].role == "admin"
    store.remove_member(org.org_id, BOB)
    assert store.get_member(org.org_id, BOB) is None
    assert store.list_user_orgs(BOB) == []


def test_a_person_cant_be_added_twice(store):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(AlreadyExists):
        store.add_member(org.org_id, ALICE, "member")
    assert store.get_member(org.org_id, ALICE).role == "admin"


def test_changing_the_role_of_a_non_member_is_not_found(store):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(NotFound):
        store.set_role(org.org_id, BOB, "admin")


@pytest.mark.parametrize("role", ["owner", "", None])
def test_unknown_roles_are_refused(store, role):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(ValueError):
        store.add_member(org.org_id, BOB, role)


# Invites

def test_an_invite_is_found_by_email_ignoring_case_and_accepted_once(store, clock):
    org = store.create_org("Acme", ALICE)
    store.create_invite(org.org_id, "Bob@Example.com", "member", ALICE, clock.now + 3600)
    assert [i.email for i in store.list_invites(org.org_id)] == ["bob@example.com"]
    assert [i.org_id for i in store.invites_for_email(" BOB@example.com ")] == [org.org_id]

    member = store.accept_invite(org.org_id, "bob@example.com", BOB)
    assert (member.sub, member.role) == (BOB, "member")
    assert store.list_invites(org.org_id) == []
    assert store.invites_for_email("bob@example.com") == []
    with pytest.raises(NotFound):
        store.accept_invite(org.org_id, "bob@example.com", CAROL)


def test_an_expired_invite_is_hidden_and_cant_be_accepted(store, clock):
    org = store.create_org("Acme", ALICE)
    store.create_invite(org.org_id, "bob@example.com", "admin", ALICE, clock.now + 10)
    clock.now += 10
    assert store.list_invites(org.org_id) == []
    assert store.invites_for_email("bob@example.com") == []
    with pytest.raises(NotFound):
        store.accept_invite(org.org_id, "bob@example.com", BOB)
    assert store.get_member(org.org_id, BOB) is None


def test_an_invite_for_another_organization_cant_be_accepted(store, clock):
    org = store.create_org("Acme", ALICE)
    other = store.create_org("Other", CAROL)
    store.create_invite(org.org_id, "bob@example.com", "member", ALICE, clock.now + 100)
    with pytest.raises(NotFound):
        store.accept_invite(other.org_id, "bob@example.com", BOB)


def test_accepting_an_invite_when_already_a_member_is_refused(store, clock):
    org = store.create_org("Acme", ALICE)
    store.create_invite(org.org_id, "alice@example.com", "member", ALICE, clock.now + 100)
    with pytest.raises(AlreadyExists):
        store.accept_invite(org.org_id, "alice@example.com", ALICE)
    assert store.get_member(org.org_id, ALICE).role == "admin"


def test_deleting_an_invite(store, clock):
    org = store.create_org("Acme", ALICE)
    store.create_invite(org.org_id, "bob@example.com", "member", ALICE, clock.now + 100)
    store.delete_invite(org.org_id, "BOB@example.com")
    assert store.invites_for_email("bob@example.com") == []


@pytest.mark.parametrize("email", ["not-an-email", "a b@example.com", "", None])
def test_invalid_emails_are_refused(store, clock, email):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(ValueError):
        store.create_invite(org.org_id, email, "member", ALICE, clock.now + 100)


# Connections

def test_connections_keep_their_settings_status_and_secret_path(store, clock):
    org = store.create_org("Acme", ALICE)
    settings = {"base_url": "https://acme.atlassian.net", "space": "CRA", "page_limit": 50,
                "labels": ["cra", "release"], "options": {"attachments": True}}
    conn = store.create_connection(org.org_id, "confluence", settings, secret_path="/p/orgs/x/conn/y")
    assert conn.status == "active" and conn.created_at == clock.now
    assert store.get_connection(org.org_id, conn.conn_id) == conn
    assert store.list_connections(org.org_id) == [conn]

    updated = store.update_connection(org.org_id, conn.conn_id, status="disconnected",
                                      settings={**settings, "space": "OPS"})
    assert updated.status == "disconnected" and updated.settings["space"] == "OPS"
    assert updated.secret_path == "/p/orgs/x/conn/y"
    assert store.get_connection(org.org_id, conn.conn_id) == updated

    store.delete_connection(org.org_id, conn.conn_id)
    assert store.get_connection(org.org_id, conn.conn_id) is None


def test_changing_returned_settings_doesnt_change_the_store(store):
    org = store.create_org("Acme", ALICE)
    conn = store.create_connection(org.org_id, "github", {"repository": "acme/app"})
    conn.settings["repository"] = "evil/other"
    assert store.get_connection(org.org_id, conn.conn_id).settings == {"repository": "acme/app"}


def test_updating_a_missing_connection_is_not_found(store):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(NotFound):
        store.update_connection(org.org_id, str(uuid.uuid4()), status="active")
    with pytest.raises(ValueError):
        store.update_connection(org.org_id, str(uuid.uuid4()), status="broken")


@pytest.mark.parametrize("type", ["GitHub", "", "a" * 40, "git hub"])
def test_bad_connection_types_are_refused(store, type):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(ValueError):
        store.create_connection(org.org_id, type, {})


# Products and releases

def test_products_and_releases(store):
    org = store.create_org("Acme", ALICE)
    rules = {"SBOM": {"conn": "c1", "tag": "{release}", "path": "sbom.cdx.json"}}
    store.put_product(org.org_id, "prd-88", rules)
    store.put_release(org.org_id, "prd-88", "v5.0-live", {"SBOM": {"conn": "c1", "ref": "abc123"}})
    store.put_release(org.org_id, "prd-88", "v5.1-live", {"Test Logs": {"conn": "c2", "build": 7}})
    store.put_product(org.org_id, "prd-880", {})
    store.put_release(org.org_id, "prd-880", "v1", {})

    assert store.get_product(org.org_id, "prd-88").rules == rules
    assert [p.product_id for p in store.list_products(org.org_id)] == ["prd-88", "prd-880"]
    assert store.get_release(org.org_id, "prd-88", "v5.1-live").pins == {"Test Logs": {"conn": "c2", "build": 7}}
    # "prd-88" releases don't include those of "prd-880"
    assert [r.release for r in store.list_releases(org.org_id, "prd-88")] == ["v5.0-live", "v5.1-live"]

    store.delete_release(org.org_id, "prd-88", "v5.0-live")
    assert store.get_release(org.org_id, "prd-88", "v5.0-live") is None
    store.delete_product(org.org_id, "prd-88")
    assert store.get_product(org.org_id, "prd-88") is None
    assert store.list_releases(org.org_id, "prd-88") == []
    assert store.get_release(org.org_id, "prd-880", "v1") is not None


@pytest.mark.parametrize("name", ["", "a#b", "../x", "a/b", "a..b", "-start", "x" * 129, "a b"])
def test_unsafe_product_and_release_ids_are_refused(store, name):
    org = store.create_org("Acme", ALICE)
    with pytest.raises(ValueError):
        store.put_product(org.org_id, name, {})
    with pytest.raises(ValueError):
        store.put_release(org.org_id, "prd-88", name, {})


# Keeping organizations apart

def test_one_organizations_data_is_invisible_from_another(store, clock):
    acme = store.create_org("Acme", ALICE)
    other = store.create_org("Other", BOB)
    conn = store.create_connection(acme.org_id, "github", {"repository": "acme/app"})
    store.put_product(acme.org_id, "app", {})
    store.put_release(acme.org_id, "app", "v1", {})
    store.create_invite(acme.org_id, "carol@example.com", "member", ALICE, clock.now + 100)

    assert store.get_member(other.org_id, ALICE) is None
    assert store.get_connection(other.org_id, conn.conn_id) is None
    assert store.list_connections(other.org_id) == []
    assert store.get_product(other.org_id, "app") is None
    assert store.get_release(other.org_id, "app", "v1") is None
    assert store.list_invites(other.org_id) == []
    with pytest.raises(NotFound):
        store.update_connection(other.org_id, conn.conn_id, status="disconnected")
    # Deleting through the wrong organization leaves the real one alone
    store.delete_connection(other.org_id, conn.conn_id)
    assert store.get_connection(acme.org_id, conn.conn_id) is not None


@pytest.mark.parametrize("org_id", ["", "ORG#x", "not-a-uuid", str(uuid.uuid4()).upper(), "../x"])
def test_malformed_organization_ids_are_refused(store, org_id):
    with pytest.raises(ValueError):
        store.get_org(org_id)
    with pytest.raises(ValueError):
        store.list_connections(org_id)


@pytest.mark.parametrize("sub", ["", "a#b", "x" * 129, "line\nbreak"])
def test_malformed_user_ids_are_refused(store, sub):
    with pytest.raises(ValueError):
        store.create_org("Acme", sub)


# DynamoDB specifics

def test_deleting_a_large_organization_works_past_one_batch(aws_env, clock):
    client = boto3.client("dynamodb", region_name="us-east-2")
    make_table(client)
    store = DynamoTenantStore(TABLE, client=client, clock=clock)
    org = store.create_org("Big", ALICE)
    for i in range(30):
        store.add_member(org.org_id, f"sub-{i}", "member")
        store.create_connection(org.org_id, "github", {"n": i})
    store.delete_org(org.org_id)
    assert client.scan(TableName=TABLE)["Count"] == 0


def test_invites_carry_a_ttl_attribute_for_dynamodb_expiry(aws_env, clock):
    client = boto3.client("dynamodb", region_name="us-east-2")
    make_table(client)
    store = DynamoTenantStore(TABLE, client=client, clock=clock)
    org = store.create_org("Acme", ALICE)
    store.create_invite(org.org_id, "bob@example.com", "member", ALICE, clock.now + 60)
    items = client.scan(TableName=TABLE)["Items"]
    invites = [item for item in items if item["SK"]["S"].startswith("INVITE#") or item["PK"]["S"].startswith("INVITE#")]
    assert len(invites) == 2 and all(item["ttl"]["N"] == str(clock.now + 60) for item in invites)
