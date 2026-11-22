"""A company's connections, products, releases and bundles (org_sources.py, source_pages.py and the
company path through app.py and artifact_pipeline.py, step 5.3).

Two companies, Acme (Alice admin, Bob member) and Other (Carol admin), share one server. Each one's
tokens, connections, products and bundles must be invisible and unusable to the other, and a signed-in
company must never be served the pod-test data."""
import io
import os
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import artifact_pipeline  # noqa: E402
import conftest  # noqa: E402
import auth  # noqa: E402
import org_sources  # noqa: E402
from accounts import AccountError, Accounts  # noqa: E402
from app import app, bundles  # noqa: E402
from connectors.base import FetchResult  # noqa: E402
from org_sources import MAX_CONNECTIONS, OrgSources, check_token  # noqa: E402
from safe_http import Response, SafeHttpError  # noqa: E402
from secret_store import InMemorySecretStore, SecretNotFound  # noqa: E402
from tenant_store import InMemoryTenantStore  # noqa: E402
from test_accounts import BASE, EMAILS, Person  # noqa: E402
from test_auth import CONFIG, KEY, FakeJwks, jwk_of, make_token  # noqa: E402

ALICE, BOB, CAROL = (f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 4))
SHA = "a" * 40
GITHUB_TOKEN = "github_pat_ACME_" + "x" * 40          # nosec B105  # fakes for tests
OTHER_TOKEN = "github_pat_OTHER_" + "y" * 40          # nosec B105
JENKINS_TOKEN = "11aa22bb33cc44dd55ee66ff77"          # nosec B105
SBOM = b'{"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}'


class TokenChecks:
    """Stands in for check_token: accepts github_pat_ tokens, records what it was asked."""

    def __init__(self):
        self.calls = []
        self.answer = None

    def __call__(self, conn_type, settings, token):
        self.calls.append((conn_type, dict(settings), token))
        return self.answer


@pytest.fixture
def world():
    store, secrets, checks = InMemoryTenantStore(), InMemorySecretStore(), TokenChecks()
    accounts = Accounts(store)
    sources = OrgSources(accounts, secrets, checks)
    acme = accounts.create_org(ALICE, "Acme")
    accounts.invite(acme.org_id, ALICE, EMAILS[BOB], "member")
    accounts.accept_invite(acme.org_id, BOB, EMAILS[BOB])
    other = accounts.create_org(CAROL, "Other")
    return {"store": store, "secrets": secrets, "checks": checks, "accounts": accounts, "sources": sources,
            "acme": acme.org_id, "other": other.org_id}


def github(sources, org, actor=ALICE, repo="acme/docs", token=GITHUB_TOKEN):
    return sources.add_connection(org, actor, "github", {"repository": repo, "token": token})


def refused(status, call, *args):
    with pytest.raises(AccountError) as error:
        call(*args)
    assert error.value.status == status, str(error.value)
    return str(error.value)


# --- connections ---

def test_a_token_goes_to_the_secret_store_and_never_into_the_connection(world):
    conn = github(world["sources"], world["acme"])
    assert conn.settings == {"repository": "acme/docs"}
    assert conn.secret_path == f"/team22-cra-test/orgs/{world['acme']}/conn/{conn.conn_id}"
    assert world["secrets"].get_connection_secret(world["acme"], conn.conn_id).reveal() == GITHUB_TOKEN
    assert GITHUB_TOKEN not in repr(conn) and GITHUB_TOKEN not in repr(world["store"].list_connections(world["acme"]))
    assert world["checks"].calls == [("github", {"repository": "acme/docs"}, GITHUB_TOKEN)]


def test_a_token_that_fails_its_check_is_never_stored(world):
    world["checks"].answer = "That token can't read acme/docs."
    assert "can't read" in refused(400, github, world["sources"], world["acme"])
    assert world["store"].list_connections(world["acme"]) == []


def test_a_failed_secret_write_leaves_no_connection(world):
    class Broken(InMemorySecretStore):
        def put_connection_secret(self, org_id, conn_id, value):
            raise RuntimeError("SSM is down")
    sources = OrgSources(world["accounts"], Broken(), world["checks"])
    refused(503, github, sources, world["acme"])
    assert world["store"].list_connections(world["acme"]) == []


@pytest.mark.parametrize("form, message", [
    ({"repository": "not a repo", "token": GITHUB_TOKEN}, "repository"),
    ({"repository": "acme/docs", "token": ""}, "Enter the"),
    ({"repository": "acme/docs", "token": "has spaces in it"}, "isn't valid"),
    ({"repository": "acme/docs", "token": GITHUB_TOKEN, "extra": "x"}, None),
])
def test_bad_connection_forms_are_refused(world, form, message):
    if message is None:   # unknown form fields are ignored, not stored
        conn = world["sources"].add_connection(world["acme"], ALICE, "github", form)
        assert conn.settings == {"repository": "acme/docs"}
        return
    assert message in refused(400, world["sources"].add_connection, world["acme"], ALICE, "github", form)


@pytest.mark.parametrize("conn_type", ["fixtures", "example-http", "nope"])
def test_companies_can_only_add_real_source_types(world, conn_type):
    refused(400, world["sources"].add_connection, world["acme"], ALICE, conn_type, {})


def test_only_admins_add_connections_and_only_members_see_them(world):
    s = world["sources"]
    refused(403, github, s, world["acme"], BOB)
    refused(404, github, s, world["acme"], CAROL)
    conn = github(s, world["acme"])
    assert [c.conn_id for c in s.connections(world["acme"], BOB)] == [conn.conn_id]
    refused(404, s.connections, world["acme"], CAROL)
    refused(404, s.connection, world["acme"], CAROL, conn.conn_id)
    # Another company's connection id doesn't resolve in your own organization either
    refused(404, s.connection, world["other"], CAROL, conn.conn_id)


def test_an_organization_has_at_most_ten_connections(world):
    for n in range(MAX_CONNECTIONS):
        github(world["sources"], world["acme"], repo=f"acme/repo{n}")
    refused(403, github, world["sources"], world["acme"], ALICE, "acme/one-more")


def test_editing_keeps_the_token_unless_a_new_one_is_given(world):
    s = world["sources"]
    conn = s.add_connection(world["acme"], ALICE, "jenkins",
                            {"url": "https://ci.acme.example", "user": "bot", "token": JENKINS_TOKEN})
    s.edit_connection(world["acme"], ALICE, conn.conn_id, {"url": "https://ci.acme.example/", "user": "bot2"})
    assert world["store"].get_connection(world["acme"], conn.conn_id).settings["user"] == "bot2"
    assert world["secrets"].get_connection_secret(world["acme"], conn.conn_id).reveal() == JENKINS_TOKEN
    s.edit_connection(world["acme"], ALICE, conn.conn_id,
                      {"url": "https://ci.acme.example", "user": "bot2", "token": "new-token-value"})
    assert world["secrets"].get_connection_secret(world["acme"], conn.conn_id).reveal() == "new-token-value"


def test_pointing_a_stored_token_at_another_server_needs_the_token_again(world):
    s = world["sources"]
    conn = s.add_connection(world["acme"], ALICE, "jenkins",
                            {"url": "https://ci.acme.example", "user": "bot", "token": JENKINS_TOKEN})
    message = refused(400, s.edit_connection, world["acme"], ALICE, conn.conn_id,
                      {"url": "https://attacker.example", "user": "bot"})
    assert "enter the api token again" in message.lower()
    assert world["store"].get_connection(world["acme"], conn.conn_id).settings["url"] == "https://ci.acme.example"
    s.edit_connection(world["acme"], ALICE, conn.conn_id,
                      {"url": "https://ci2.acme.example", "user": "bot", "token": "token-for-ci2"})
    assert world["store"].get_connection(world["acme"], conn.conn_id).settings["url"] == "https://ci2.acme.example"


def test_changing_a_github_repository_rechecks_the_stored_token(world):
    s = world["sources"]
    conn = github(s, world["acme"])
    world["checks"].answer = "That token can't read acme/secret."
    refused(400, s.edit_connection, world["acme"], ALICE, conn.conn_id, {"repository": "acme/secret"})
    assert world["checks"].calls[-1] == ("github", {"repository": "acme/secret"}, GITHUB_TOKEN)
    assert world["store"].get_connection(world["acme"], conn.conn_id).settings == {"repository": "acme/docs"}


def test_only_admins_edit_or_delete(world):
    s = world["sources"]
    conn = github(s, world["acme"])
    refused(403, s.edit_connection, world["acme"], BOB, conn.conn_id, {"repository": "acme/docs"})
    refused(403, s.delete_connection, world["acme"], BOB, conn.conn_id)
    refused(404, s.delete_connection, world["other"], CAROL, conn.conn_id)
    assert world["store"].get_connection(world["acme"], conn.conn_id) is not None


def test_a_connection_in_use_cant_be_deleted(world):
    s = world["sources"]
    conn = github(s, world["acme"])
    s.create_product(world["acme"], ALICE, "widget")
    s.create_release(world["acme"], ALICE, "widget", "v1.0")
    s.set_entry(world["acme"], ALICE, "widget", "v1.0", "SBOM", conn.conn_id,
                {"revision": SHA, "paths": "sbom.json"})
    assert "release widget v1.0" in refused(409, s.delete_connection, world["acme"], ALICE, conn.conn_id)
    s.clear_entry(world["acme"], ALICE, "widget", "v1.0", "SBOM")
    s.delete_connection(world["acme"], ALICE, conn.conn_id)
    assert world["store"].get_connection(world["acme"], conn.conn_id) is None
    with pytest.raises(SecretNotFound):
        world["secrets"].get_connection_secret(world["acme"], conn.conn_id)


# --- the GitHub token check ---

class FakeClient:
    answer = None

    def __init__(self, hosts, **options):
        assert list(hosts) == ["api.github.com"]

    def get(self, url, headers=None):
        FakeClient.seen = (url, headers)
        if isinstance(FakeClient.answer, Exception):
            raise FakeClient.answer
        return FakeClient.answer


@pytest.mark.parametrize("answer, ok, phrase", [
    (Response(200, {}, b"{}"), True, None),
    (Response(401, {}, b""), False, "didn't accept"),
    (Response(404, {}, b""), False, "can't read acme/docs"),
    (Response(403, {}, b""), False, "can't read acme/docs"),
    (Response(403, {"x-ratelimit-remaining": "0"}, b""), False, "rate limiting"),
    (Response(502, {}, b""), False, "HTTP 502"),
    (SafeHttpError("timeout", "timed out"), False, "couldn't be reached"),
])
def test_a_github_token_is_checked_with_one_read_of_the_repository(monkeypatch, answer, ok, phrase):
    monkeypatch.setattr(org_sources, "SafeHttpClient", FakeClient)
    FakeClient.answer = answer
    result = check_token("github", {"repository": "acme/docs"}, GITHUB_TOKEN)
    assert (result is None) == ok
    if phrase:
        assert phrase in result and GITHUB_TOKEN not in result
    url, headers = FakeClient.seen
    assert url == "https://api.github.com/repos/acme/docs"
    assert headers["Authorization"] == f"Bearer {GITHUB_TOKEN}"


@pytest.mark.parametrize("token", ["ghp_classic000000000000000000000000000000", "gho_oauth", "plain"])
def test_classic_github_tokens_are_refused_without_a_request(monkeypatch, token):
    monkeypatch.setattr(org_sources, "SafeHttpClient", None)   # any request would fail
    assert "fine-grained" in check_token("github", {"repository": "acme/docs"}, token)


def test_other_types_arent_checked_on_save():
    assert check_token("jenkins", {"url": "https://ci.example", "user": "u"}, "t") is None


# --- products and releases ---

def test_products_rules_and_pins(world):
    s = world["sources"]
    conn = github(s, world["acme"])
    s.create_product(world["acme"], ALICE, "widget")
    s.set_entry(world["acme"], ALICE, "widget", None, "SBOM", conn.conn_id,
                {"revision": SHA, "paths": "artifacts/{product}/{release}/sbom.json\nsbom.json"})
    rules = world["store"].get_product(world["acme"], "widget").rules
    assert rules == {"SBOM": {"conn": conn.conn_id, "revision": SHA,
                              "paths": ["artifacts/{product}/{release}/sbom.json", "sbom.json"]}}
    s.create_release(world["acme"], ALICE, "widget", "v2")
    s.set_entry(world["acme"], ALICE, "widget", "v2", "SBOM", conn.conn_id, {"revision": "b" * 40, "paths": "x.json"})
    assert world["store"].get_release(world["acme"], "widget", "v2").pins["SBOM"]["revision"] == "b" * 40
    s.clear_entry(world["acme"], ALICE, "widget", None, "SBOM")
    assert world["store"].get_product(world["acme"], "widget").rules == {}


def test_refs_are_validated_by_their_connector(world):
    s = world["sources"]
    conn = github(s, world["acme"])
    s.create_product(world["acme"], ALICE, "widget")
    for form in ({"revision": "main", "paths": "a.json"}, {"revision": SHA, "paths": ""}, {"revision": SHA}):
        refused(400, s.set_entry, world["acme"], ALICE, "widget", None, "SBOM", conn.conn_id, form)
    refused(400, s.set_entry, world["acme"], ALICE, "widget", None, "Not a category", conn.conn_id,
            {"revision": SHA, "paths": "a"})


def test_a_rule_cant_use_another_companys_connection(world):
    s = world["sources"]
    theirs = github(s, world["other"], actor=CAROL, repo="other/docs", token=OTHER_TOKEN)
    s.create_product(world["acme"], ALICE, "widget")
    assert "this organization's connections" in refused(
        400, s.set_entry, world["acme"], ALICE, "widget", None, "SBOM", theirs.conn_id, {"revision": SHA, "paths": "a"})


def test_only_admins_change_products_and_only_members_read_them(world):
    s = world["sources"]
    refused(403, s.create_product, world["acme"], BOB, "widget")
    s.create_product(world["acme"], ALICE, "widget")
    assert [p.product_id for p in s.products(world["acme"], BOB)] == ["widget"]
    refused(404, s.products, world["acme"], CAROL)
    refused(404, s.product, world["other"], CAROL, "widget")
    refused(403, s.create_release, world["acme"], BOB, "widget", "v1")
    refused(403, s.delete_product, world["acme"], BOB, "widget")


@pytest.mark.parametrize("name", ["", "has space", "../x", "a#b", "x" * 129])
def test_bad_product_and_release_names_are_refused(world, name):
    refused(400, world["sources"].create_product, world["acme"], ALICE, name)


def test_duplicates_are_refused_and_deleting_a_product_removes_its_releases(world):
    s = world["sources"]
    s.create_product(world["acme"], ALICE, "widget")
    refused(409, s.create_product, world["acme"], ALICE, "widget")
    s.create_release(world["acme"], ALICE, "widget", "v1")
    refused(409, s.create_release, world["acme"], ALICE, "widget", "v1")
    refused(404, s.create_release, world["acme"], ALICE, "gadget", "v1")
    s.delete_product(world["acme"], ALICE, "widget")
    assert world["store"].list_releases(world["acme"], "widget") == []


# --- bundles through the API ---

class FakeSources:
    """Stands in for every connector: serves SBOM to whichever company's token reads acme/docs."""

    def __init__(self):
        self.reads = []

    def __call__(self, connector, conn, ref, *, deadline, budget, secrets):
        token = secrets.get_connection_secret(conn.org_id, conn.conn_id).reveal()
        self.reads.append((conn.org_id, conn.settings.get("repository"), token, dict(ref)))
        if conn.settings.get("repository") == "acme/docs" and token == GITHUB_TOKEN:
            return FetchResult.retrieved(SBOM, "sbom.json", version=ref["revision"])
        return FetchResult.not_found("GitHub has none of the pinned files.")


@pytest.fixture
def site(world, monkeypatch):
    fake = FakeSources()
    monkeypatch.setattr(artifact_pipeline, "run_connector", fake)
    state = auth.AuthState(CONFIG, auth.TokenVerifier(CONFIG, FakeJwks(jwk_of(KEY, "k1"))), cognito=None,
                           clock=time.time, accounts=world["accounts"], secrets=world["secrets"],
                           token_check=world["checks"])
    old_key = app.secret_key
    app.secret_key = "test-secret-key"  # nosec B105
    app.extensions["cra_auth"] = state
    bundles.clear()
    s = world["sources"]
    conn = github(s, world["acme"])
    s.create_product(world["acme"], ALICE, "widget")
    s.create_release(world["acme"], ALICE, "widget", "v1.0")
    s.set_entry(world["acme"], ALICE, "widget", "v1.0", "SBOM", conn.conn_id,
                {"revision": SHA, "paths": "artifacts/{product}/{release}/sbom.json"})
    world.update(fake=fake, conn=conn)
    try:
        yield world
    finally:
        app.extensions.pop("cra_auth", None)
        app.secret_key = old_key


def bearer(sub):
    return {"Authorization": f"Bearer {make_token(sub=sub)}"}


def request(headers, org=None, product="widget", release="v1.0"):
    path = f"/orgs/{org}/bundles" if org else "/bundles"
    return app.test_client().post(path, json={"product_id": product, "release_version": release},
                                  headers=headers, base_url=BASE)


def manifest_of(body):
    return {item["category"]: item for item in body["manifest"]}


def test_a_company_gets_a_bundle_from_its_own_connection(site):
    response = request(bearer(ALICE), org=site["acme"])
    assert response.status_code == 202
    ack = response.get_json()
    assert "_org_id" not in ack
    body = app.test_client().get(f"/bundles/{ack['bundle_id']}", headers=bearer(BOB), base_url=BASE).get_json()
    assert "_org_id" not in body
    items = manifest_of(body)
    assert items["SBOM"]["status"] == "PRESENT" and items["SBOM"]["external_source"] == "GitHub"
    assert {c: items[c]["status"] for c in ("Risk Assessment", "Test Logs", "User Instructions")} == \
        dict.fromkeys(("Risk Assessment", "Test Logs", "User Instructions"), "MISSING")
    assert site["fake"].reads == [(site["acme"], "acme/docs", GITHUB_TOKEN,
                                   {"revision": SHA, "paths": ["artifacts/widget/v1.0/sbom.json"]})]
    archive = app.test_client().get(f"/bundles/{ack['bundle_id']}/download", headers=bearer(ALICE), base_url=BASE)
    assert archive.status_code == 200
    assert zipfile.ZipFile(io.BytesIO(archive.data)).read("sbom.json") == SBOM


def test_another_company_cant_read_or_download_the_bundle(site):
    bundle_id = request(bearer(ALICE), org=site["acme"]).get_json()["bundle_id"]
    unknown = app.test_client().get("/bundles/00000000-0000-4000-8000-000000000000", base_url=BASE)
    for headers in (bearer(CAROL), {}, {"Authorization": "Bearer phase3-local-token"}):
        for path in (f"/bundles/{bundle_id}", f"/bundles/{bundle_id}/download"):
            response = app.test_client().get(path, headers=headers, base_url=BASE)
            assert response.status_code == 404
        assert response.get_json() == {"error": "Bundle archive not found"}
    assert unknown.status_code == 404


def test_another_company_cant_request_bundles_for_it(site):
    assert request(bearer(CAROL), org=site["acme"]).status_code == 404
    assert request({}, org=site["acme"]).status_code == 401
    assert site["fake"].reads == []


def test_a_company_never_gets_pod_test_data(site):
    """prd-88 v1.2 is the shared pod-test release; a signed-in company that hasn't defined it gets FAILED."""
    ack = request(bearer(ALICE), org=site["acme"], product="prd-88", release="v1.2").get_json()
    body = app.test_client().get(f"/bundles/{ack['bundle_id']}", headers=bearer(ALICE), base_url=BASE).get_json()
    assert body["status"] == "FAILED" and body["artifacts"] == [] and body["manifest"] == []
    assert body["error"] == "No documents found for prd-88 v1.2"
    # The same request with no login is the pod-test release, as it always was
    ack = request({}, product="prd-88", release="v1.2").get_json()
    assert app.test_client().get(f"/bundles/{ack['bundle_id']}", base_url=BASE).get_json()["status"] == "COMPLETED"


def test_another_companys_release_with_the_same_name_is_separate(site):
    s = site["sources"]
    theirs = github(s, site["other"], actor=CAROL, repo="other/docs", token=OTHER_TOKEN)
    s.create_product(site["other"], CAROL, "widget")
    s.create_release(site["other"], CAROL, "widget", "v1.0")
    s.set_entry(site["other"], CAROL, "widget", "v1.0", "SBOM", theirs.conn_id, {"revision": SHA, "paths": "sbom.json"})
    ack = request(bearer(CAROL), org=site["other"]).get_json()
    body = app.test_client().get(f"/bundles/{ack['bundle_id']}", headers=bearer(CAROL), base_url=BASE).get_json()
    assert manifest_of(body)["SBOM"]["status"] == "INACCESSIBLE"
    assert site["fake"].reads == [(site["other"], "other/docs", OTHER_TOKEN, {"revision": SHA, "paths": ["sbom.json"]})]


def test_a_signed_in_caller_must_say_which_organization(site):
    assert request(bearer(ALICE)).status_code == 400
    response = request(bearer(ALICE))
    assert "orgs/<org_id>/bundles" in response.get_json()["error"]


def test_a_browser_session_uses_its_active_organization(site):
    alice = Person(ALICE)
    alice.set_active(site["acme"])
    response = alice.http.post("/bundles", json={"product_id": "widget", "release_version": "v1.0"}, base_url=BASE)
    assert response.status_code == 202
    assert bundles.get(response.get_json()["bundle_id"])["_org_id"] == site["acme"]
    carol = Person(CAROL)
    carol.set_active(site["acme"])   # a forged active org in the cookie
    response = carol.http.post("/bundles", json={"product_id": "widget", "release_version": "v1.0"}, base_url=BASE)
    assert response.status_code == 404


def test_a_removed_member_loses_the_companys_bundles(site):
    bundle_id = request(bearer(BOB), org=site["acme"]).get_json()["bundle_id"]
    site["accounts"].remove_member(site["acme"], ALICE, BOB)
    assert app.test_client().get(f"/bundles/{bundle_id}", headers=bearer(BOB), base_url=BASE).status_code == 404


def test_a_deleted_connection_makes_its_category_inaccessible_not_missing(site):
    site["store"].delete_connection(site["acme"], site["conn"].conn_id)   # bypassing the in-use check
    ack = request(bearer(ALICE), org=site["acme"]).get_json()
    body = app.test_client().get(f"/bundles/{ack['bundle_id']}", headers=bearer(ALICE), base_url=BASE).get_json()
    assert manifest_of(body)["SBOM"]["status"] == "INACCESSIBLE"
    assert "no longer exists" in manifest_of(body)["SBOM"]["source_failure_detail"]


def test_a_secret_store_outage_is_inaccessible_not_an_error(site, monkeypatch):
    def down(org_id, conn_id):
        raise RuntimeError("SSM unavailable")
    monkeypatch.setattr(site["secrets"], "get_connection_secret", down)
    # The real connector code this time, on a network that fails the test if anything is sent
    monkeypatch.setattr(artifact_pipeline, "run_connector", conftest.REAL_RUN_CONNECTOR)
    artifact_pipeline.CONNECTOR_HTTP_OPTIONS = {"resolver": lambda host, port: ["203.0.113.9"],
                                                "connection_factory": _refusing}
    try:
        ack = request(bearer(ALICE), org=site["acme"]).get_json()
    finally:
        artifact_pipeline.CONNECTOR_HTTP_OPTIONS = None
    body = app.test_client().get(f"/bundles/{ack['bundle_id']}", headers=bearer(ALICE), base_url=BASE).get_json()
    assert body["status"] == "PARTIAL_SUCCESS"
    assert manifest_of(body)["SBOM"]["status"] == "INACCESSIBLE"


def _refusing(host, address, port, timeout):
    raise AssertionError("no request may be sent without a token")


def test_the_pod_test_path_is_unchanged_for_anonymous_callers(site):
    response = request({}, product="prd-88", release="v1.3")
    assert response.status_code == 202
    body = app.test_client().get(f"/bundles/{response.get_json()['bundle_id']}", base_url=BASE).get_json()
    assert body["status"] == "PARTIAL_SUCCESS"


# --- the pages ---

def test_an_admin_sets_up_a_source_and_gets_a_bundle_on_the_pages(site):
    alice = Person(ALICE)
    alice.set_active(site["acme"])
    response = alice.post("/app/connections/new/jenkins", url="https://ci.acme.example", user="bot",
                          token=JENKINS_TOKEN)
    assert response.headers["Location"].endswith("/app/connections")
    page = alice.get("/app/connections").data
    assert b"Jenkins" in page and b"ci.acme.example" in page and JENKINS_TOKEN.encode() not in page
    conn = next(c for c in site["store"].list_connections(site["acme"]) if c.type == "jenkins")
    detail = alice.get(f"/app/connections/{conn.conn_id}").data
    assert JENKINS_TOKEN.encode() not in detail and b'type="password"' in detail

    alice.post("/app/products", product_id="gadget")
    entry_page = alice.get(f"/app/products/gadget/entry/SBOM?conn={site['conn'].conn_id}").data
    assert b'name="revision"' in entry_page and b'name="paths"' in entry_page
    alice.post("/app/products/gadget/entry/SBOM", conn=site["conn"].conn_id, revision=SHA,
               paths="artifacts/{product}/{release}/sbom.json")
    assert b"artifacts/{product}/{release}/sbom.json" in alice.get("/app/products/gadget").data

    response = alice.post("/app/bundles", product_id="gadget", release_version="v9")
    assert "/app/bundles/" in response.headers["Location"]
    page = alice.get(response.headers["Location"].split(BASE)[-1])
    assert page.status_code == 200
    assert b"PARTIAL_SUCCESS" in page.data and b"Download the bundle" in page.data
    assert site["fake"].reads[-1][3] == {"revision": SHA, "paths": ["artifacts/gadget/v9/sbom.json"]}


def test_members_see_the_setup_but_cant_change_it(site):
    bob = Person(BOB)
    bob.set_active(site["acme"])
    page = bob.get("/app/connections").data
    assert b"GitHub" in page and b"Add a" not in page
    assert b"Set</a>" not in bob.get("/app/products/widget").data
    bob.post("/app/connections/new/github", repository="acme/x", token=GITHUB_TOKEN)
    bob.post("/app/products", product_id="sneaky")
    assert len(site["store"].list_connections(site["acme"])) == 1
    assert [p.product_id for p in site["store"].list_products(site["acme"])] == ["widget"]
    # but members can request bundles
    response = bob.post("/app/bundles", product_id="widget", release_version="v1.0")
    assert "/app/bundles/" in response.headers["Location"]


def test_another_company_sees_none_of_it_on_the_pages(site):
    carol = Person(CAROL)
    carol.set_active(site["other"])
    assert b"acme/docs" not in carol.get("/app/connections").data
    assert carol.get(f"/app/connections/{site['conn'].conn_id}").status_code == 302
    assert carol.get("/app/products/widget").status_code == 302
    alice = Person(ALICE)
    alice.set_active(site["acme"])
    location = alice.post("/app/bundles", product_id="widget", release_version="v1.0").headers["Location"]
    assert carol.get(location.split(BASE)[-1]).status_code == 302
    carol.post(f"/app/connections/{site['conn'].conn_id}/delete")
    assert site["store"].get_connection(site["acme"], site["conn"].conn_id) is not None


def test_connection_pages_need_csrf(site):
    alice = Person(ALICE)
    alice.set_active(site["acme"])
    response = alice.http.post("/app/connections/new/github", data={"repository": "acme/x", "token": GITHUB_TOKEN},
                               base_url=BASE, headers={"Referer": f"{BASE}/app/"})
    assert response.status_code == 400
    assert len(site["store"].list_connections(site["acme"])) == 1
