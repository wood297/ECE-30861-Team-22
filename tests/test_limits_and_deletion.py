# ruff: noqa: F811  # store, clock and aws_env are pytest fixtures shared with test_tenant_store
"""Limits and deletion (rate_limits.py, the Bedrock cap, and deleting organizations and accounts;
Phase 5 plan §3 and §6, step 5.4).

Deletion runs on both stores (in memory, and DynamoDB on moto) and then searches everything left
for the deleted organization's id, the person's id and their email: nothing may remain."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import artifact_pipeline  # noqa: E402
import auth  # noqa: E402
import rate_limits  # noqa: E402
from accounts import AccountError, Accounts  # noqa: E402
from app import app, bundles  # noqa: E402
from artifact_classifier import DailyLimitReached, classify_artifact  # noqa: E402
from artifact_formats import validate_artifact  # noqa: E402
from artifact_pipeline import CappedProvider  # noqa: E402
from bundle_service import archives  # noqa: E402
from connectors.base import FetchResult  # noqa: E402
from org_sources import OrgSources  # noqa: E402
from rate_limits import DailyQuota, RateLimiter  # noqa: E402
from secret_store import InMemorySecretStore  # noqa: E402
from tenant_store import DELETED_USER, DynamoTenantStore, InMemoryTenantStore  # noqa: E402
from test_accounts import BASE, Person  # noqa: E402
from test_auth import CONFIG, KEY, FakeJwks, jwk_of, make_token  # noqa: E402
from test_tenant_store import aws_env, clock, store  # noqa: E402,F401  # fixtures: both stores

ALICE, BOB, CAROL, DAVE = (f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 5))
EMAIL = {ALICE: "alice@acme.example", BOB: "bob@acme.example", CAROL: "carol@acme.example",
         DAVE: "dave@solo.example"}
SHA = "c" * 40
TOKEN = "github_pat_DELETE_ME_" + "z" * 30   # nosec B105  # a fake for tests


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def limits_on():
    rate_limits.set_enabled(True)
    yield
    rate_limits.set_enabled(False)


# --- the limiters on their own ---

def test_a_rate_limiter_allows_its_limit_per_window_per_key():
    clock = Clock()
    limiter = RateLimiter(3, window=60, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == pytest.approx(60)
    assert limiter.hit("b") is None
    clock.now += 30
    assert limiter.hit("a") == pytest.approx(30)
    clock.now += 30.01
    assert limiter.hit("a") is None


def test_refused_requests_dont_extend_the_wait():
    clock = Clock()
    limiter = RateLimiter(1, window=60, clock=clock)
    limiter.hit("a")
    for _ in range(100):
        limiter.hit("a")
    clock.now += 60.01
    assert limiter.hit("a") is None


def test_a_rate_limiter_forgets_the_quietest_callers_first():
    clock = Clock()
    limiter = RateLimiter(1, window=60, clock=clock, max_keys=3)
    for key in "abc":
        limiter.hit(key)
    limiter.hit("a")          # a is now the most recent
    limiter.hit("d")          # pushes out b, the quietest
    assert limiter.hit("b") is None
    assert limiter.hit("a") is not None


def test_a_daily_quota_resets_at_midnight_utc():
    clock = Clock(1_800_000_000.0)   # 2027-01-15 08:00 UTC
    quota = DailyQuota(2, clock=clock)
    assert [quota.take("org"), quota.take("org"), quota.take("org")] == [True, True, False]
    assert quota.take("other") is True
    clock.now += 16 * 3600            # past midnight
    assert quota.take("org") is True


# --- limits on the bundle API ---

def post(headers=None, path="/bundles", ip="198.51.100.7", body=None):
    return app.test_client().post(path, json=body or {"product_id": "prd-88", "release_version": "v1.2"},
                                  headers=headers or {}, base_url=BASE, environ_base={"REMOTE_ADDR": ip})


def test_requests_without_a_login_are_limited_per_ip_address(limits_on):
    bundles.clear()
    for _ in range(rate_limits.POD_TEST_BUNDLES_PER_MINUTE):
        assert post().status_code == 202
    refused = post()
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert "120 a minute" in refused.get_json()["error"]
    assert post(ip="198.51.100.8").status_code == 202


def test_a_full_tester_run_fits_well_inside_the_limit():
    """The shared suite makes far fewer bundle requests than the per-IP limit."""
    from test_runner import suite
    source = open(suite.__file__).read()
    assert source.count("create_bundle(") + source.count('"/bundles"') < rate_limits.POD_TEST_BUNDLES_PER_MINUTE


def test_bad_requests_dont_use_up_the_limit(limits_on):
    for _ in range(rate_limits.POD_TEST_BUNDLES_PER_MINUTE + 5):
        assert post(body={"product_id": ""}).status_code == 400
    assert post().status_code == 202


@pytest.fixture
def company(monkeypatch):
    accounts = Accounts(InMemoryTenantStore())
    secrets = InMemorySecretStore()
    state = auth.AuthState(CONFIG, auth.TokenVerifier(CONFIG, FakeJwks(jwk_of(KEY, "k1"))), cognito=None,
                           clock=time.time, accounts=accounts, secrets=secrets, token_check=lambda *a: None)
    old_key = app.secret_key
    app.secret_key = "test-secret-key"  # nosec B105
    app.extensions["cra_auth"] = state
    acme = accounts.create_org(ALICE, "Acme").org_id
    other = accounts.create_org(CAROL, "Other").org_id
    sources = OrgSources(accounts, secrets, lambda *a: None)
    conn = sources.add_connection(acme, ALICE, "github", {"repository": "acme/docs", "token": TOKEN})
    sources.create_product(acme, ALICE, "widget")
    sources.set_entry(acme, ALICE, "widget", None, "SBOM", conn.conn_id, {"revision": SHA, "paths": "sbom.json"})
    monkeypatch.setattr(artifact_pipeline, "run_connector", lambda *a, **k: FetchResult.retrieved(
        b'{"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}', "sbom.json", version=SHA))
    bundles.clear()
    try:
        yield {"accounts": accounts, "secrets": secrets, "state": state, "acme": acme, "other": other,
               "conn": conn, "sources": sources}
    finally:
        app.extensions.pop("cra_auth", None)
        app.secret_key = old_key


def bearer(sub):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def test_a_company_is_limited_per_organization_not_per_address(company, limits_on):
    path = f"/orgs/{company['acme']}/bundles"
    widget = {"product_id": "widget", "release_version": "v1"}
    for n in range(rate_limits.ORG_BUNDLES_PER_MINUTE):
        assert post(bearer(ALICE), path, ip=f"203.0.113.{n}", body=widget).status_code == 202
    refused = post(bearer(ALICE), path, ip="203.0.113.250", body=widget)
    assert refused.status_code == 429 and "30 a minute" in refused.get_json()["error"]
    # Another company, and pod-test, have their own limits
    company["sources"].create_product(company["other"], CAROL, "gadget")
    assert post(bearer(CAROL), f"/orgs/{company['other']}/bundles", body={"product_id": "gadget",
                                                                          "release_version": "v1"}).status_code == 202
    assert post().status_code == 202


def test_the_bundle_page_is_limited_too(company, limits_on):
    alice = Person(ALICE)
    alice.set_active(company["acme"])
    for _ in range(rate_limits.ORG_BUNDLES_PER_MINUTE):
        rate_limits.org_bundles.hit(company["acme"])
    response = alice.post("/app/bundles", product_id="widget", release_version="v1")
    assert "/app/bundles/" not in response.headers["Location"]
    assert b"Too many bundle requests" in alice.get("/app/products").data


# --- the Bedrock cap ---

class CountingProvider:
    model_id = "fake-model"

    def __init__(self):
        self.calls = 0

    def classify(self, *, system_prompt, artifact_json, response_schema):
        self.calls += 1
        return json.dumps({"actual_category": "SBOM", "ambiguous": False, "evidence": "lists components"})


def test_a_company_gets_a_daily_number_of_bedrock_calls(limits_on):
    real = CountingProvider()
    capped = CappedProvider(real, "org-a")
    document = validate_artifact(b"# Components\n\nlibfoo 1.0\n", "markdown")
    for _ in range(rate_limits.BEDROCK_CALLS_PER_DAY):
        assert classify_artifact(document, "SBOM", capped).error_code != "daily_limit"
    over = classify_artifact(document, "SBOM", capped)
    assert (over.decision, over.error_code) == ("FAILURE", "daily_limit")
    assert real.calls == rate_limits.BEDROCK_CALLS_PER_DAY
    assert classify_artifact(document, "SBOM", CappedProvider(real, "org-b")).error_code != "daily_limit"
    assert capped.model_id == "fake-model"


def test_a_capped_provider_refuses_before_calling_bedrock(limits_on):
    real = CountingProvider()
    rate_limits.bedrock_calls.limit, old = 0, rate_limits.bedrock_calls.limit
    try:
        with pytest.raises(DailyLimitReached):
            CappedProvider(real, "org-a").classify(system_prompt="", artifact_json="{}", response_schema={})
    finally:
        rate_limits.bedrock_calls.limit = old
    assert real.calls == 0


# --- deleting an organization ---

def leftovers(store, *needles):
    """Every stored item mentioning any needle: DynamoDB scanned in full, or the in-memory dicts."""
    if isinstance(store, DynamoTenantStore):
        items = store._client.scan(TableName=store.table)["Items"]
        texts = [json.dumps(item) for item in items]
    else:
        texts = [repr(item) for table in (store._orgs, store._members, store._invites, store._connections,
                                          store._products, store._releases) for item in table.items()]
    return [text for text in texts if any(needle in text for needle in needles)]


def populated(store, clock):
    accounts = Accounts(store, clock=clock)
    secrets = InMemorySecretStore()
    sources = OrgSources(accounts, secrets, lambda *a: None)
    acme = accounts.create_org(ALICE, "Acme").org_id
    for sub in (BOB, CAROL):
        accounts.invite(acme, ALICE, EMAIL[sub], "member")
        accounts.accept_invite(acme, sub, EMAIL[sub])
    accounts.invite(acme, ALICE, "pending@acme.example", "member")
    conn = sources.add_connection(acme, ALICE, "github", {"repository": "acme/docs", "token": TOKEN})
    sources.create_product(acme, ALICE, "widget")
    sources.set_entry(acme, ALICE, "widget", None, "SBOM", conn.conn_id, {"revision": SHA, "paths": "a.json"})
    sources.create_release(acme, ALICE, "widget", "v1")
    sources.set_entry(acme, ALICE, "widget", "v1", "SBOM", conn.conn_id, {"revision": SHA, "paths": "b.json"})
    solo = accounts.create_org(DAVE, "Solo").org_id
    return accounts, secrets, acme, solo, conn


def test_deleting_an_organization_leaves_nothing_behind(store, clock):
    accounts, secrets, acme, solo, conn = populated(store, clock)
    assert leftovers(store, acme)
    deleted = accounts.delete_org(acme, ALICE, secrets, "Acme")
    assert deleted.name == "Acme"
    assert leftovers(store, acme) == []
    assert secrets.delete_org_secrets(acme) == 0          # nothing left to delete
    assert store.list_user_orgs(BOB) == [] and store.invites_for_email("pending@acme.example") == []
    assert store.count_orgs_created_by(ALICE) == 0
    # The other organization is untouched
    assert store.get_org(solo) is not None and store.get_member(solo, DAVE) is not None


def test_only_an_admin_deletes_and_only_with_the_name_typed(store, clock):
    accounts, secrets, acme, solo, _ = populated(store, clock)
    with pytest.raises(AccountError) as error:
        accounts.delete_org(acme, BOB, secrets, "Acme")
    assert error.value.status == 403
    with pytest.raises(AccountError) as error:
        accounts.delete_org(acme, DAVE, secrets, "Acme")
    assert error.value.status == 404
    with pytest.raises(AccountError):
        accounts.delete_org(acme, ALICE, secrets, "acme")
    assert store.get_org(acme) is not None


def test_if_the_tokens_cant_be_deleted_nothing_is(store, clock):
    accounts, _, acme, _, conn = populated(store, clock)

    class Broken(InMemorySecretStore):
        def delete_org_secrets(self, org_id):
            raise RuntimeError("SSM is down")
    with pytest.raises(AccountError) as error:
        accounts.delete_org(acme, ALICE, Broken(), "Acme")
    assert error.value.status == 503
    assert store.get_connection(acme, conn.conn_id) is not None


def test_deleting_on_the_page_also_drops_bundles_and_cached_files(company):
    alice = Person(ALICE)
    alice.set_active(company["acme"])
    alice.post("/app/bundles", product_id="widget", release_version="v1")
    owned = [b for b in list(bundles) if (bundles.get(b) or {}).get("_org_id") == company["acme"]]
    assert owned and archives.get(owned[0]) is not None
    artifact_pipeline._pinned[(company["acme"], "conn", "{}", "{}")] = FetchResult.not_found("x")
    pod_test = post().get_json()["bundle_id"]

    response = alice.post("/app/org/delete", confirm_name="Acme")
    assert response.headers["Location"].endswith("/app/")
    assert company["accounts"].store.get_org(company["acme"]) is None
    assert all(bundles.get(b) is None and archives.get(b) is None for b in owned)
    assert not [k for k in artifact_pipeline._pinned if k[0] == company["acme"]]
    assert bundles.get(pod_test) is not None
    assert alice.session()["org_id"] is None


def test_the_page_needs_the_name_and_an_admin(company):
    bob = Person(BOB)
    company["accounts"].invite(company["acme"], ALICE, "bob@acme.example", "member")
    company["accounts"].accept_invite(company["acme"], BOB, "bob@acme.example")
    bob.set_active(company["acme"])
    bob.post("/app/org/delete", confirm_name="Acme")
    alice = Person(ALICE)
    alice.set_active(company["acme"])
    alice.post("/app/org/delete", confirm_name="Wrong")
    assert company["accounts"].store.get_org(company["acme"]) is not None


# --- deleting an account ---

def test_deleting_an_account_leaves_nothing_that_names_the_person(store, clock):
    accounts, secrets, acme, solo, _ = populated(store, clock)
    accounts.set_role(acme, ALICE, CAROL, "admin")
    accounts.invite(acme, CAROL, "carols-friend@acme.example", "member")
    accounts.invite(solo, DAVE, EMAIL[CAROL], "member")   # an invite to Carol, from another org

    deleted = accounts.delete_account(CAROL, EMAIL[CAROL], secrets)
    assert deleted == []
    assert leftovers(store, CAROL, EMAIL[CAROL]) == []
    assert store.get_member(acme, CAROL) is None
    invite = next(i for i in store.list_invites(acme) if i.email == "carols-friend@acme.example")
    assert invite.invited_by == DELETED_USER      # the invite stays; it's the organization's
    assert store.get_org(acme) is not None


def test_an_organization_where_you_are_the_only_member_goes_with_you(store, clock):
    accounts, secrets, acme, solo, _ = populated(store, clock)
    secrets.put_connection_secret(solo, "6f1c1d2e-0000-4000-8000-000000000009", "solo-token")
    assert accounts.delete_account(DAVE, EMAIL[DAVE], secrets) == [solo]
    assert leftovers(store, solo, DAVE) == []
    assert secrets.delete_org_secrets(solo) == 0
    assert store.get_org(acme) is not None


def test_the_only_admin_of_a_shared_organization_cant_delete_their_account(store, clock):
    accounts, secrets, acme, _, _ = populated(store, clock)
    with pytest.raises(AccountError) as error:
        accounts.delete_account(ALICE, EMAIL[ALICE], secrets)
    assert error.value.status == 409 and "Acme" in str(error.value)
    assert store.get_member(acme, ALICE) is not None
    accounts.set_role(acme, ALICE, BOB, "admin")
    accounts.delete_account(ALICE, EMAIL[ALICE], secrets)
    assert store.get_org(acme).created_by == DELETED_USER
    assert leftovers(store, ALICE) == []


class FakeDirectory:
    def __init__(self, fail=False):
        self.deleted, self.fail = [], fail

    def describe(self, sub):
        return {"email": EMAIL.get(sub, ""), "name": "Someone"}

    def delete_user(self, sub):
        if self.fail:
            raise RuntimeError("Cognito is down")
        self.deleted.append(sub)


def test_deleting_your_account_on_the_page(company):
    directory = FakeDirectory()
    company["state"].directory = directory
    dave = Person(DAVE, verified=False)
    company["accounts"].create_org(DAVE, "Dave's")
    page = dave.get("/app/account")
    assert b"Delete my account" in page.data
    dave.post("/app/account/delete", confirm="nope")
    assert directory.deleted == []
    response = dave.post("/app/account/delete", confirm="Delete my account")
    assert response.status_code == 302 and "/logout?" in response.headers["Location"]
    assert directory.deleted == [DAVE]
    assert company["accounts"].my_orgs(DAVE) == []
    assert "sub" not in dave.session()


def test_the_account_page_explains_what_blocks_deletion(company):
    company["accounts"].invite(company["acme"], ALICE, "bob@acme.example", "member")
    company["accounts"].accept_invite(company["acme"], BOB, "bob@acme.example")
    alice = Person(ALICE)
    page = alice.get("/app/account").data
    assert b"only admin of" in page and b"Acme" in page and b'name="confirm"' not in page


def test_if_cognito_cant_delete_the_sign_in_you_are_told_and_can_retry(company):
    company["state"].directory = FakeDirectory(fail=True)
    dave = Person(DAVE)
    response = dave.post("/app/account/delete", confirm="delete my account")
    assert response.headers["Location"].endswith("/app/account")
    assert b"couldn" in dave.get("/app/account").data
    company["state"].directory = FakeDirectory()
    assert "/logout?" in dave.post("/app/account/delete", confirm="delete my account").headers["Location"]


def test_cognito_user_deletion_treats_already_gone_as_done():
    from accounts import CognitoDirectory

    class UserNotFoundException(Exception):
        pass

    class Idp:
        def __init__(self, error=None):
            self.error, self.calls = error, []

        def admin_delete_user(self, UserPoolId, Username):
            self.calls.append((UserPoolId, Username))
            if self.error:
                raise self.error
    idp = Idp()
    CognitoDirectory("us-east-2_Pool", "us-east-2", client=idp).delete_user(ALICE)
    assert idp.calls == [("us-east-2_Pool", ALICE)]
    CognitoDirectory("us-east-2_Pool", "us-east-2", client=Idp(UserNotFoundException())).delete_user(ALICE)
    with pytest.raises(RuntimeError):
        CognitoDirectory("us-east-2_Pool", "us-east-2", client=Idp(RuntimeError("throttled"))).delete_user(ALICE)


# --- the privacy page ---

def test_the_privacy_page_says_what_is_stored_and_how_to_delete_it(company):
    page = app.test_client().get("/privacy", base_url=BASE)
    assert page.status_code == 200
    for phrase in (b"us-east-2", b"Cognito", b"DynamoDB", b"Parameter Store", b"Bedrock", b"Deleting your data"):
        assert phrase in page.data
