"""Opt-in live check of company accounts against the -dev AWS resources (Phase 5 plan, §7.6, step 5.5).
Never run in CI; pytest covers the same rules with fakes.

It creates two throwaway Cognito users (no email is sent) and two throwaway organizations, runs
them through the account features against the real Cognito pool, DynamoDB table and SSM
parameters, then deletes everything and checks that nothing is left. If a step fails, it still
cleans up, and the leftover check says whether that worked.

Run with the same settings as the app, signed in to AWS (`aws login`):

    AWS_REGION=us-east-2 CRA_COGNITO_POOL_ID=... CRA_COGNITO_CLIENT_ID=... CRA_COGNITO_DOMAIN=... \
    CRA_TENANT_TABLE=team22-cra-tenants-dev CRA_SECRET_PREFIX=/team22-cra-dev python tenant_live_check.py

It refuses to run unless the table and the secret prefix are the -dev ones. Output is one JSON
line per check with its name and whether it passed; no token, secret or email is printed.
Exit 0: every check passed; 1: at least one failed; 2: bad configuration.
"""
import json
import os
import secrets as random
import sys
import time
import traceback
import uuid
from typing import Any, Callable

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from accounts import AccountError, CognitoDirectory
from artifact_pipeline import CompanyAccount, account_sources
from auth import TokenError, load_from_env
from org_sources import OrgSources
from secret_store import SecretNotFound
from tenant_store import DELETED_USER, Organization

PRODUCT, RELEASE, CATEGORY = "live-check", "v1", "Test Logs"


class Check:
    def __init__(self) -> None:
        self.passed = True

    def __call__(self, name: str, ok: Any, detail: str = "") -> bool:
        ok = bool(ok)
        self.passed = self.passed and ok
        line = {"check": name, "ok": ok}
        if detail and not ok:
            line["detail"] = detail
        print(json.dumps(line), flush=True)
        return ok

    def raises(self, name: str, action: Callable[[], Any], statuses: tuple[int, ...]) -> bool:
        try:
            action()
        except AccountError as error:
            return self(name, error.status in statuses, f"HTTP {error.status}")
        return self(name, False, "it was allowed")


def configuration() -> tuple[Any, str, str, str] | None:
    """(the app's auth state, region, table, secret prefix), or None if it isn't the -dev setup."""
    table = os.environ.get("CRA_TENANT_TABLE", "")
    prefix = os.environ.get("CRA_SECRET_PREFIX", "")
    if not table.endswith("-dev") or not prefix.endswith("-dev") or os.environ.get("CRA_LOCAL_SECRETS"):
        print(json.dumps({"error": "set CRA_TENANT_TABLE and CRA_SECRET_PREFIX to the -dev resources "
                                   "(and not CRA_LOCAL_SECRETS)"}))
        return None
    loaded = load_from_env(os.environ)
    if loaded is None:
        print(json.dumps({"error": "the app couldn't start accounts with these settings; see the log line above"}))
        return None
    state, _ = loaded
    return state, state.config.region, table, prefix


def forged_token(issuer: str, client_id: str) -> str:
    """An access token that claims our pool but is signed with a key Cognito never issued."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {"iss": issuer, "sub": str(uuid.uuid4()), "token_use": "access", "client_id": client_id,
              "iat": now, "exp": now + 300}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "live-check-" + uuid.uuid4().hex})


def create_user(cognito: Any, pool_id: str, email: str) -> str:
    """A confirmed-looking user with a verified email, without sending them anything. Returns its sub."""
    response = cognito.admin_create_user(
        UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"},
                        {"Name": "name", "Value": "Live check"}])
    return next(a["Value"] for a in response["User"]["Attributes"] if a["Name"] == "sub")


def user_exists(cognito: Any, pool_id: str, sub: str) -> bool:
    try:
        cognito.admin_get_user(UserPoolId=pool_id, Username=sub)
    except cognito.exceptions.UserNotFoundException:
        return False
    return True


def scan_for(dynamo: Any, table: str, needles: list[str]) -> int:
    """Items anywhere in the table that mention any of the needles. The -dev table is small."""
    found, kwargs = 0, {"TableName": table, "ConsistentRead": True}
    while True:
        response = dynamo.scan(**kwargs)
        for item in response.get("Items", []):
            text = json.dumps(item)
            found += any(needle in text for needle in needles)
        if "LastEvaluatedKey" not in response:
            return found
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def parameters_under(ssm: Any, path: str) -> int:
    response = ssm.get_parameters_by_path(Path=path, Recursive=True, WithDecryption=False)
    return len(response.get("Parameters", []))


def run(check: Check, state: Any, region: str, table: str, prefix: str) -> None:
    import boto3
    cognito = boto3.client("cognito-idp", region_name=region)
    dynamo = boto3.client("dynamodb", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    pool_id = state.config.pool_id
    accounts, store, secrets = state.accounts, state.accounts.store, state.secrets
    directory = CognitoDirectory(pool_id, region, client=cognito, ttl=0.001)
    # The token is made up, so skip the check that would try it against Jenkins
    sources = OrgSources(accounts, secrets, token_check=lambda *_: None)

    run_id = uuid.uuid4().hex[:8]
    email_a, email_b, email_c = (f"live-check-{run_id}-{who}@example.com" for who in "abc")
    token = "live-check-" + random.token_urlsafe(24)
    subs: list[str] = []
    orgs: list[str] = []
    try:
        check("app secrets read from SSM and the table opened", True)

        keys = state.verifier._fetch_jwks_https().get("keys") or []
        check("the pool's public keys download", any(k.get("kty") == "RSA" for k in keys))
        try:
            state.verifier.verify_access_token(forged_token(state.config.issuer, state.config.client_id))
            check("a token signed with someone else's key is refused", False, "it was accepted")
        except TokenError:
            check("a token signed with someone else's key is refused", True)

        sub_a = create_user(cognito, pool_id, email_a)
        subs.append(sub_a)
        sub_b = create_user(cognito, pool_id, email_b)
        subs.append(sub_b)
        check("two throwaway Cognito users created", sub_a != sub_b)
        check("the members page can look up a user's email", (directory.describe(sub_b) or {}).get("email") == email_b)

        org_a = accounts.create_org(sub_a, f"Live check {run_id} A").org_id
        orgs.append(org_a)
        accounts.invite(org_a, sub_a, email_b, "member")
        check("an invite reaches the invited email", [i.org.org_id for i in accounts.my_invites(email_b)] == [org_a])
        check.raises("someone else's email can't accept it", lambda: accounts.accept_invite(org_a, sub_b, email_a),
                     (403, 404))
        accounts.accept_invite(org_a, sub_b, email_b)
        roles = {m.sub: m.role for m in accounts.members(org_a, sub_a)}
        check("the invited person joins as a member", roles == {sub_a: "admin", sub_b: "member"})

        org_b = accounts.create_org(sub_b, f"Live check {run_id} B").org_id
        orgs.append(org_b)

        conn = sources.add_connection(org_a, sub_a, "jenkins",
                                      {"url": "https://jenkins.example.com", "user": "live-check", "token": token})
        check("the token is stored in SSM under the organization",
              conn.secret_path == f"{prefix}/orgs/{org_a}/conn/{conn.conn_id}"
              and secrets.get_connection_secret(org_a, conn.conn_id).reveal() == token)
        check("the token isn't in DynamoDB", scan_for(dynamo, table, [token]) == 0)
        check.raises("a member can't add a connection",
                     lambda: sources.add_connection(org_a, sub_b, "jenkins", {"url": "https://jenkins.example.com",
                                                                              "user": "x", "token": token}), (403,))

        sources.create_product(org_a, sub_a, PRODUCT)
        sources.create_release(org_a, sub_a, PRODUCT, RELEASE)
        sources.set_entry(org_a, sub_a, PRODUCT, None, CATEGORY, conn.conn_id,
                          {"job": "live-check", "build": "1", "paths": "logs.md"})
        company_a = CompanyAccount(org_a, store, secrets)
        found_conn, _ = (account_sources(company_a, PRODUCT, RELEASE) or {}).get(CATEGORY, (None, {}))
        check("a bundle for the release finds the rule and its connection",
              found_conn is not None and found_conn.conn_id == conn.conn_id)
        check("a bundle can read the connection's token",
              company_a.secrets is not None
              and company_a.secrets.get_connection_secret(org_a, conn.conn_id).reveal() == token)

        company_b = CompanyAccount(org_b, store, secrets)
        check("another company's bundle doesn't see the product",
              account_sources(company_b, PRODUCT, RELEASE) is None
              and company_b.get_connection(org_a, conn.conn_id) is None)
        check.raises("A's admin can't read B's products", lambda: sources.products(org_b, sub_a), (403, 404))
        check.raises("A's admin can't read B's connections", lambda: sources.connections(org_b, sub_a), (403, 404))

        # B's organization outlives B: A joins it as an admin, and B leaves an invite pending there
        accounts.invite(org_b, sub_b, email_a, "admin")
        accounts.accept_invite(org_b, sub_a, email_a)
        accounts.invite(org_b, sub_b, email_c, "member")

        check.raises("A's only admin can't delete their account", lambda: accounts.delete_account(sub_a, email_a,
                     secrets), (409,))
        check("B deletes their account: no organization was theirs alone",
              accounts.delete_account(sub_b, email_b, secrets) == [])
        check("B has left both organizations",
              [m.sub for m in accounts.members(org_a, sub_a)] == [sub_a]
              and [m.sub for m in accounts.members(org_b, sub_a)] == [sub_a])
        check("B's organization no longer names B as its creator",
              (store.get_org(org_b) or Organization("", "", sub_b, 0)).created_by == DELETED_USER)
        check("B's pending invite no longer names B as its sender",
              [i.invited_by for i in accounts.invites(org_b, sub_a)] == [DELETED_USER])
        directory.delete_user(sub_b)
        check("B's Cognito user is gone", not user_exists(cognito, pool_id, sub_b))

        check.raises("deleting A needs its exact name", lambda: accounts.delete_org(org_a, sub_a, secrets, "wrong"),
                     (400,))
        accounts.delete_org(org_a, sub_a, secrets, f"Live check {run_id} A")
        check("A's token is deleted from SSM", parameters_under(ssm, f"{prefix}/orgs/{org_a}") == 0)
        try:
            secrets.get_connection_secret(org_a, conn.conn_id)
            check("A's token can't be read any more", False, "it was still there")
        except SecretNotFound:
            check("A's token can't be read any more", True)

        check("A deletes their account, and B's old organization, now theirs alone, goes with it",
              accounts.delete_account(sub_a, email_a, secrets) == [org_b])
        directory.delete_user(sub_a)
    except Exception as error:  # report it, then still clean up and check for leftovers
        # Only the error's type and where it happened: its message could quote a value
        line = next((f.lineno for f in reversed(traceback.extract_tb(error.__traceback__))
                     if f.filename == __file__), 0)
        check("the run finished", False, f"{type(error).__name__} at tenant_live_check.py:{line}")
    finally:
        cleanup(cognito, pool_id, store, secrets, subs, orgs)

    needles = orgs + subs + [email_a, email_b, email_c, token]
    check("nothing about either organization or person is left in DynamoDB", scan_for(dynamo, table, needles) == 0)
    check("no tokens are left in SSM", all(parameters_under(ssm, f"{prefix}/orgs/{org}") == 0 for org in orgs))
    check("both Cognito users are gone", not any(user_exists(cognito, pool_id, sub) for sub in subs))


def cleanup(cognito: Any, pool_id: str, store: Any, secrets: Any, subs: list[str], orgs: list[str]) -> None:
    """Best effort, and harmless when the run already deleted everything."""
    for org in orgs:
        try:
            secrets.delete_org_secrets(org)
            store.delete_org(org)
        except Exception:  # nosec B110  # the leftover checks report anything that stays
            pass
    for sub in subs:
        try:
            store.forget_creator(sub)
            for membership in store.list_user_orgs(sub):
                store.remove_member(membership.org_id, sub)
        except Exception:  # nosec B110
            pass
        try:
            cognito.admin_delete_user(UserPoolId=pool_id, Username=sub)
        except Exception:  # nosec B110
            pass


def main() -> int:
    loaded = configuration()
    if loaded is None:
        return 2
    check = Check()
    run(check, *loaded)
    return 0 if check.passed else 1


if __name__ == "__main__":
    sys.exit(main())
