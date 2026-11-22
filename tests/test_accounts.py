"""Organizations, members, invites and roles (accounts.py and account_pages.py, step 5.2).

The rules run on the in-memory store here; tests/test_tenant_store.py already runs the same store
contract on DynamoDB (moto). The page tests sign several people in at once and check that none of
them can see or change another organization."""
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import auth  # noqa: E402
from accounts import (INVITE_DAYS, MAX_MEMBERS, MAX_ORGS_CREATED, AccountError, Accounts,  # noqa: E402
                      CognitoDirectory)
from app import app  # noqa: E402
from tenant_store import InMemoryTenantStore  # noqa: E402

ALICE, BOB, CAROL, MALLORY = (f"0000000{n}-0000-4000-8000-000000000000" for n in range(1, 5))
EMAILS = {ALICE: "alice@acme.example", BOB: "bob@acme.example", CAROL: "carol@other.example",
          MALLORY: "mallory@evil.example"}
BASE = "https://team22.example.test"
NOT_AN_ORG = "11111111-1111-4111-8111-111111111111"


class Clock:
    def __init__(self):
        self.now = 1_800_000_000

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(clock):
    return Accounts(InMemoryTenantStore(clock=clock), clock=clock)


def org_with(accounts, admin=ALICE, members=(), name="Acme"):
    org = accounts.create_org(admin, name)
    for sub in members:
        accounts.invite(org.org_id, admin, EMAILS[sub], "member")
        accounts.accept_invite(org.org_id, sub, EMAILS[sub])
    return org


def refused(status, call, *args):
    with pytest.raises(AccountError) as error:
        call(*args)
    assert error.value.status == status, str(error.value)
    return str(error.value)


# --- organizations ---

def test_creating_an_organization_makes_you_its_admin(accounts):
    org = accounts.create_org(ALICE, "  Acme GmbH  ")
    assert org.name == "Acme GmbH"
    assert accounts.member(org.org_id, ALICE).role == "admin"
    assert [(m.org.name, m.role) for m in accounts.my_orgs(ALICE)] == [("Acme GmbH", "admin")]


def test_a_person_can_create_at_most_three_organizations(accounts):
    for n in range(MAX_ORGS_CREATED):
        accounts.create_org(ALICE, f"Org {n}")
    assert accounts.can_create(ALICE) == 0
    refused(403, accounts.create_org, ALICE, "One too many")
    # Joining other organizations doesn't count against it
    org = org_with(accounts, admin=BOB, members=[ALICE])
    assert accounts.member(org.org_id, ALICE).role == "member"


def test_leaving_doesnt_give_back_a_creation(accounts):
    orgs = [accounts.create_org(ALICE, f"Org {n}") for n in range(MAX_ORGS_CREATED)]
    accounts.invite(orgs[0].org_id, ALICE, EMAILS[BOB], "admin")
    accounts.accept_invite(orgs[0].org_id, BOB, EMAILS[BOB])
    accounts.remove_member(orgs[0].org_id, ALICE, ALICE)
    refused(403, accounts.create_org, ALICE, "Another")


@pytest.mark.parametrize("name", ["", "   ", "x" * 101, "tab\there"])
def test_bad_organization_names_are_refused(accounts, name):
    refused(400, accounts.create_org, ALICE, name)


def test_only_an_admin_can_rename(accounts):
    org = org_with(accounts, members=[BOB])
    refused(403, accounts.rename_org, org.org_id, BOB, "Bob's now")
    assert accounts.rename_org(org.org_id, ALICE, "Acme AG").name == "Acme AG"


# --- invites ---

def test_an_invite_lasts_seven_days_and_only_its_email_can_accept_it(accounts, clock):
    org = org_with(accounts)
    invite = accounts.invite(org.org_id, ALICE, "Bob@Acme.Example ", "member")
    assert invite.email == "bob@acme.example"
    assert invite.expires_at == clock.now + INVITE_DAYS * 24 * 3600
    refused(404, accounts.accept_invite, org.org_id, MALLORY, EMAILS[MALLORY])
    assert [i.org.name for i in accounts.my_invites(EMAILS[BOB])] == ["Acme"]
    assert accounts.accept_invite(org.org_id, BOB, EMAILS[BOB]).role == "member"
    assert accounts.my_invites(EMAILS[BOB]) == []
    refused(409, accounts.accept_invite, org.org_id, BOB, EMAILS[BOB])


def test_an_expired_invite_cant_be_accepted(accounts, clock):
    org = org_with(accounts)
    accounts.invite(org.org_id, ALICE, EMAILS[BOB], "member")
    clock.now += INVITE_DAYS * 24 * 3600 + 1
    assert accounts.my_invites(EMAILS[BOB]) == []
    refused(404, accounts.accept_invite, org.org_id, BOB, EMAILS[BOB])


def test_without_a_verified_email_there_are_no_invites(accounts):
    org = org_with(accounts)
    accounts.invite(org.org_id, ALICE, EMAILS[BOB], "member")
    assert accounts.my_invites(None) == []
    refused(403, accounts.accept_invite, org.org_id, BOB, None)


def test_an_email_domain_never_grants_membership(accounts):
    org = org_with(accounts)
    accounts.invite(org.org_id, ALICE, EMAILS[BOB], "member")
    refused(404, accounts.accept_invite, org.org_id, CAROL, "someone-else@acme.example")


def test_only_admins_invite(accounts):
    org = org_with(accounts, members=[BOB])
    refused(403, accounts.invite, org.org_id, BOB, EMAILS[CAROL], "member")
    refused(404, accounts.invite, org.org_id, MALLORY, EMAILS[CAROL], "admin")


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "two words@acme.example", "@acme.example"])
def test_bad_invite_emails_are_refused(accounts, email):
    org = org_with(accounts)
    refused(400, accounts.invite, org.org_id, ALICE, email, "member")


def test_bad_invite_roles_are_refused(accounts):
    org = org_with(accounts)
    refused(400, accounts.invite, org.org_id, ALICE, EMAILS[BOB], "owner")


def test_an_admin_can_withdraw_an_invite(accounts):
    org = org_with(accounts, members=[BOB])
    accounts.invite(org.org_id, ALICE, EMAILS[CAROL], "member")
    refused(403, accounts.cancel_invite, org.org_id, BOB, EMAILS[CAROL])
    accounts.cancel_invite(org.org_id, ALICE, EMAILS[CAROL])
    refused(404, accounts.accept_invite, org.org_id, CAROL, EMAILS[CAROL])


def test_declining_removes_only_your_own_invite(accounts):
    org = org_with(accounts)
    accounts.invite(org.org_id, ALICE, EMAILS[BOB], "member")
    accounts.invite(org.org_id, ALICE, EMAILS[CAROL], "member")
    accounts.decline_invite(org.org_id, EMAILS[BOB])
    assert [i.email for i in accounts.invites(org.org_id, ALICE)] == [EMAILS[CAROL]]


def test_an_organization_holds_at_most_25_people_counting_invites(accounts):
    org = org_with(accounts)
    for n in range(MAX_MEMBERS - 1):
        accounts.invite(org.org_id, ALICE, f"person{n}@acme.example", "member")
    refused(403, accounts.invite, org.org_id, ALICE, "one-too-many@acme.example", "member")
    # Renewing an invite that's already pending is fine
    accounts.invite(org.org_id, ALICE, "person0@acme.example", "admin")
    accounts.cancel_invite(org.org_id, ALICE, "person1@acme.example")
    accounts.invite(org.org_id, ALICE, "now-there-is-room@acme.example", "member")


def test_accepting_respects_the_cap_even_with_old_invites(accounts, clock):
    store = accounts.store
    org = org_with(accounts)
    # Invites written before the cap was reached (e.g. by an earlier version) can't overfill it
    for n in range(MAX_MEMBERS):
        store.create_invite(org.org_id, f"p{n}@acme.example", "member", ALICE, clock.now + 3600)
    for n in range(MAX_MEMBERS - 1):
        accounts.accept_invite(org.org_id, f"00000000-0000-4000-8000-{n:012d}", f"p{n}@acme.example")
    refused(403, accounts.accept_invite, org.org_id, BOB, f"p{MAX_MEMBERS - 1}@acme.example")


# --- roles and the last admin ---

def test_the_last_admin_cant_leave_be_removed_or_be_demoted(accounts):
    org = org_with(accounts, members=[BOB])
    assert "last admin" in refused(409, accounts.remove_member, org.org_id, ALICE, ALICE)
    assert "last admin" in refused(409, accounts.set_role, org.org_id, ALICE, ALICE, "member")
    accounts.set_role(org.org_id, ALICE, BOB, "admin")
    # Now there are two: either can step down, but not both
    accounts.set_role(org.org_id, BOB, ALICE, "member")
    refused(409, accounts.set_role, org.org_id, BOB, BOB, "member")
    refused(403, accounts.remove_member, org.org_id, ALICE, BOB)


def test_members_can_leave_but_not_remove_others(accounts):
    org = org_with(accounts, members=[BOB, CAROL])
    refused(403, accounts.remove_member, org.org_id, BOB, CAROL)
    refused(403, accounts.set_role, org.org_id, BOB, BOB, "admin")
    accounts.remove_member(org.org_id, BOB, BOB)
    assert accounts.member(org.org_id, BOB) is None


def test_admins_can_remove_and_promote(accounts):
    org = org_with(accounts, members=[BOB, CAROL])
    accounts.set_role(org.org_id, ALICE, BOB, "admin")
    accounts.remove_member(org.org_id, BOB, CAROL)
    assert sorted(m.sub for m in accounts.members(org.org_id, ALICE)) == [ALICE, BOB]
    refused(404, accounts.remove_member, org.org_id, ALICE, CAROL)


# --- across organizations ---

def test_nobody_reads_or_changes_another_organization(accounts):
    acme = org_with(accounts, members=[BOB])
    other = org_with(accounts, admin=CAROL, name="Other")
    for call, args in [
        (accounts.members, (acme.org_id, CAROL)),
        (accounts.invites, (acme.org_id, CAROL)),
        (accounts.invite, (acme.org_id, CAROL, EMAILS[MALLORY], "admin")),
        (accounts.cancel_invite, (acme.org_id, CAROL, EMAILS[BOB])),
        (accounts.set_role, (acme.org_id, CAROL, BOB, "admin")),
        (accounts.remove_member, (acme.org_id, CAROL, BOB)),
        (accounts.rename_org, (acme.org_id, CAROL, "Owned")),
    ]:
        assert "doesn't exist or you aren't a member" in refused(404, call, *args)
    assert [m.org.name for m in accounts.my_orgs(CAROL)] == ["Other"]
    assert accounts.member(other.org_id, ALICE) is None


def test_an_unknown_or_malformed_org_id_looks_the_same_as_not_a_member(accounts):
    org_with(accounts)
    for org_id in (NOT_AN_ORG, "not-a-uuid", "ORG#x", None, 7):
        assert refused(404, accounts.require, org_id, ALICE) == \
            "That organization doesn't exist or you aren't a member."


# --- the user directory ---

class FakeCognitoIdp:
    def __init__(self):
        self.calls = 0

    def admin_get_user(self, UserPoolId, Username):
        self.calls += 1
        if Username == MALLORY:
            raise RuntimeError("UserNotFoundException")
        return {"UserAttributes": [{"Name": "email", "Value": EMAILS[Username]},
                                   {"Name": "name", "Value": Username[:8]}, {"Name": "sub", "Value": Username}]}


def test_the_directory_looks_people_up_in_cognito_and_caches_them():
    idp = FakeCognitoIdp()
    directory = CognitoDirectory("us-east-2_Pool", "us-east-2", client=idp)
    assert directory.describe(ALICE) == {"email": EMAILS[ALICE], "name": ALICE[:8]}
    directory.describe(ALICE)
    assert idp.calls == 1
    assert directory.describe(MALLORY) is None


# --- the pages ---

class FakeDirectory:
    def describe(self, sub):
        return {"email": EMAILS.get(sub, ""), "name": {ALICE: "Alice", BOB: "Bob", CAROL: "Carol"}.get(sub, "")}


@pytest.fixture
def site(accounts):
    state = auth.AuthState(auth.CognitoConfig("us-east-2_TestPool1", "testclient0123456789",
                                              "team22-cra-test.auth.us-east-2.amazoncognito.com"),
                           verifier=None, cognito=None, clock=time.time, accounts=accounts,
                           directory=FakeDirectory())
    old_key = app.secret_key
    app.secret_key = "test-secret-key"  # nosec B105
    app.extensions["cra_auth"] = state
    try:
        yield accounts
    finally:
        app.extensions.pop("cra_auth", None)
        app.secret_key = old_key


class Person:
    """A browser signed in as one person."""

    def __init__(self, sub, verified=True):
        self.sub = sub
        self.http = app.test_client()
        with self.http.session_transaction(base_url=BASE) as s:
            s.update(sub=sub, org_id=None, exp=int(time.time()) + 3600, _permanent=True)
            if verified:
                s["email"] = EMAILS[sub]

    def get(self, path):
        return self.http.get(path, base_url=BASE)

    def post(self, path, **form):
        page = self.get("/app/")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        return self.http.post(path, data={"csrf_token": token, **form}, base_url=BASE,
                              headers={"Referer": f"{BASE}/app/"})

    def session(self):
        with self.http.session_transaction(base_url=BASE) as s:
            return dict(s)

    def set_active(self, org_id):
        with self.http.session_transaction(base_url=BASE) as s:
            s["org_id"] = org_id


def created(person, name):
    response = person.post("/app/orgs", name=name)
    assert response.status_code == 302 and response.headers["Location"].endswith("/app/members")
    return person.session()["org_id"]


def test_the_pages_need_a_browser_session(site):
    anonymous = app.test_client()
    for path in ("/app/", "/app/members"):
        response = anonymous.get(path, base_url=BASE)
        assert response.status_code == 302 and response.headers["Location"].endswith("/auth/login")
    response = anonymous.post("/app/orgs", data={"name": "x"}, base_url=BASE)
    assert response.status_code == 302
    assert site.my_orgs(ALICE) == []


def test_the_pages_answer_503_when_accounts_are_off():
    assert app.test_client().get("/app/", base_url=BASE).status_code == 503


def test_creating_an_organization_on_the_page(site):
    alice = Person(ALICE)
    org_id = created(alice, "Acme GmbH")
    page = alice.get("/app/members")
    assert page.status_code == 200
    assert b"Acme GmbH" in page.data and b"Alice" in page.data and b"Invite someone" in page.data
    assert [m.org.name for m in site.my_orgs(ALICE)] == ["Acme GmbH"]
    assert site.member(org_id, ALICE).role == "admin"


def test_forms_need_a_csrf_token(site):
    alice = Person(ALICE)
    response = alice.http.post("/app/orgs", data={"name": "No token"}, base_url=BASE,
                               headers={"Referer": f"{BASE}/app/"})
    assert response.status_code == 400
    assert site.my_orgs(ALICE) == []


def test_an_invite_flow_across_two_browsers(site):
    alice, bob = Person(ALICE), Person(BOB)
    org_id = created(alice, "Acme")
    alice.post("/app/members/invite", email=EMAILS[BOB], role="member")
    assert EMAILS[BOB].encode() in alice.get("/app/members").data
    home = bob.get("/app/")
    assert b"Invites" in home.data and b"Acme" in home.data
    response = bob.post(f"/app/invites/{org_id}/accept")
    assert response.headers["Location"].endswith("/app/members")
    assert bob.session()["org_id"] == org_id
    page = bob.get("/app/members").data
    assert b"Alice" in page and b"Bob" in page
    assert b"Invite someone" not in page and b"Remove" not in page  # a member, not an admin


def test_someone_without_a_verified_email_sees_no_invites(site):
    alice, bob = Person(ALICE), Person(BOB, verified=False)
    org_id = created(alice, "Acme")
    alice.post("/app/members/invite", email=EMAILS[BOB], role="member")
    home = bob.get("/app/").data
    assert b"isn't verified" in home and b"Accept" not in home
    bob.post(f"/app/invites/{org_id}/accept")
    assert site.member(org_id, BOB) is None


def test_a_forged_active_org_in_the_cookie_gets_nothing(site):
    alice, carol = Person(ALICE), Person(CAROL)
    acme = created(alice, "Acme")
    alice.post("/app/members/invite", email=EMAILS[BOB], role="member")
    created(carol, "Other")
    # Carol's cookie names Acme. Signed cookies stop strangers, but a removed member keeps an old cookie
    carol.set_active(acme)
    page = carol.get("/app/members")
    assert page.status_code == 302 and page.headers["Location"].endswith("/app/")
    assert carol.session()["org_id"] is None
    for path, form in [("/app/members/invite", {"email": EMAILS[MALLORY], "role": "admin"}),
                       (f"/app/members/{ALICE}/role", {"role": "member"}),
                       (f"/app/members/{ALICE}/remove", {}),
                       ("/app/members/invites/cancel", {"email": EMAILS[BOB]}),
                       ("/app/org/rename", {"name": "Owned"}),
                       ("/app/leave", {})]:
        carol.set_active(acme)
        carol.post(path, **form)
    assert [(m.sub, m.role) for m in site.members(acme, ALICE)] == [(ALICE, "admin")]
    assert [i.email for i in site.invites(acme, ALICE)] == [EMAILS[BOB]]
    assert site.store.get_org(acme).name == "Acme"


def test_switching_into_an_organization_youre_not_in_is_refused(site):
    alice, carol = Person(ALICE), Person(CAROL)
    acme = created(alice, "Acme")
    carol.post(f"/app/orgs/{acme}/switch")
    assert carol.session()["org_id"] is None
    assert b"aren&#39;t a member" in carol.get("/app/").data


def test_a_removed_member_loses_access_on_their_next_request(site):
    alice, bob = Person(ALICE), Person(BOB)
    org_id = created(alice, "Acme")
    alice.post("/app/members/invite", email=EMAILS[BOB], role="member")
    bob.post(f"/app/invites/{org_id}/accept")
    alice.post(f"/app/members/{BOB}/remove")
    assert bob.get("/app/members").status_code == 302
    assert bob.session()["org_id"] is None


def test_the_last_admin_is_told_why_they_cant_leave(site):
    alice = Person(ALICE)
    org_id = created(alice, "Acme")
    response = alice.post("/app/leave")
    assert response.headers["Location"].endswith("/app/members")
    assert b"last admin" in alice.get("/app/members").data
    assert site.member(org_id, ALICE) is not None


def test_roles_change_on_the_page(site):
    alice, bob = Person(ALICE), Person(BOB)
    org_id = created(alice, "Acme")
    alice.post("/app/members/invite", email=EMAILS[BOB], role="member")
    bob.post(f"/app/invites/{org_id}/accept")
    alice.post(f"/app/members/{BOB}/role", role="admin")
    assert site.member(org_id, BOB).role == "admin"
    assert b"Invite someone" in bob.get("/app/members").data
    bob.post("/app/leave")
    assert site.member(org_id, BOB) is None and bob.session()["org_id"] is None


def test_names_from_cognito_are_escaped(site):
    class Hostile:
        def describe(self, sub):
            return {"name": "<script>alert(1)</script>", "email": "x@y.example"}
    app.extensions["cra_auth"].directory = Hostile()
    alice = Person(ALICE)
    created(alice, "<b>Acme</b>")
    page = alice.get("/app/members").data
    assert b"<script>" not in page and b"&lt;script&gt;" in page
    assert b"<b>Acme</b>" not in page


def test_the_api_isnt_affected_by_the_active_organization(site):
    alice = Person(ALICE)
    created(alice, "Acme")
    response = alice.http.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"},
                               base_url=BASE)
    assert response.status_code == 202
