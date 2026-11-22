"""tenant_live_check.py run against moto, so the script itself stays correct between live runs.
The live run against the -dev resources is still what proves the real AWS setup works."""
import json
import uuid

import boto3
import pytest

import auth
import tenant_live_check
from test_tenant_store import aws_env  # noqa: F401  # a fixture (same import path as the other tests, for mypy)

TABLE, PREFIX = "team22-cra-tenants-dev", "/team22-cra-dev"
POOL = "us-east-2_LiveCheck"


class UserNotFoundException(Exception):
    pass


class FakeCognito:
    """The four admin calls the script makes. (moto's Cognito needs a package CI doesn't install.)"""

    class exceptions:
        UserNotFoundException = UserNotFoundException

    def __init__(self):
        self.users: dict[str, dict] = {}   # sub -> attributes

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction):
        assert UserPoolId == POOL and MessageAction == "SUPPRESS"
        sub = str(uuid.uuid4())
        attributes = [*UserAttributes, {"Name": "sub", "Value": sub}]
        self.users[sub] = {a["Name"]: a["Value"] for a in attributes}
        return {"User": {"Username": sub, "Attributes": attributes}}

    def admin_get_user(self, UserPoolId, Username):
        if Username not in self.users:
            raise UserNotFoundException()
        return {"UserAttributes": [{"Name": k, "Value": v} for k, v in self.users[Username].items()]}

    def admin_delete_user(self, UserPoolId, Username):
        if self.users.pop(Username, None) is None:
            raise UserNotFoundException()


@pytest.fixture
def dev(aws_env, monkeypatch):  # noqa: F811
    boto3.client("dynamodb", region_name="us-east-2").create_table(
        TableName=TABLE, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "PK", "AttributeType": "S"},
                              {"AttributeName": "SK", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}])
    cognito = FakeCognito()
    real_client = boto3.client
    monkeypatch.setattr(boto3, "client",
                        lambda name, **kw: cognito if name == "cognito-idp" else real_client(name, **kw))
    ssm = boto3.client("ssm", region_name="us-east-2")
    for name in ("flask-secret-key", "cognito-client-secret"):
        ssm.put_parameter(Name=f"{PREFIX}/app/{name}", Value="not-a-real-secret", Type="SecureString")
    for name, value in {"CRA_COGNITO_POOL_ID": POOL, "CRA_COGNITO_CLIENT_ID": "client",
                        "CRA_COGNITO_DOMAIN": "team22-cra-dev.auth.us-east-2.amazoncognito.com",
                        "CRA_TENANT_TABLE": TABLE, "CRA_SECRET_PREFIX": PREFIX, "AWS_REGION": "us-east-2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CRA_LOCAL_SECRETS", raising=False)
    # The pool's real keys aren't reachable from moto; any RSA key stands in for them
    monkeypatch.setattr(auth.TokenVerifier, "_fetch_jwks_https",
                        lambda self: {"keys": [{"kty": "RSA", "kid": "pool-key", "n": "AQAB", "e": "AQAB"}]})
    return cognito


def results(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_every_check_passes_and_nothing_is_left(dev, capsys):
    assert tenant_live_check.main() == 0
    lines = results(capsys)
    assert len(lines) >= 25 and all(line["ok"] for line in lines), [x for x in lines if not x["ok"]]
    assert boto3.client("dynamodb", region_name="us-east-2").scan(TableName=TABLE)["Items"] == []
    assert dev.users == {}


def test_output_holds_no_token_or_email(dev, capsys, monkeypatch):
    monkeypatch.setattr(tenant_live_check.random, "token_urlsafe", lambda n: "SENTINEL-TOKEN")
    tenant_live_check.main()
    out = capsys.readouterr().out
    assert "SENTINEL-TOKEN" not in out and "@example.com" not in out and "not-a-real-secret" not in out


def test_a_failed_step_still_cleans_up(dev, capsys, monkeypatch):
    monkeypatch.setattr(tenant_live_check.OrgSources, "create_product",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tenant_live_check.main() == 1
    lines = {line["check"]: line["ok"] for line in results(capsys)}
    assert lines["the run finished"] is False
    assert lines["nothing about either organization or person is left in DynamoDB"]
    assert lines["no tokens are left in SSM"] and lines["both Cognito users are gone"]
    assert boto3.client("dynamodb", region_name="us-east-2").scan(TableName=TABLE)["Items"] == []
    assert dev.users == {}


@pytest.mark.parametrize("table,prefix", [("team22-cra-tenants", PREFIX), (TABLE, "/team22-cra")])
def test_refuses_the_production_resources(dev, capsys, monkeypatch, table, prefix):
    monkeypatch.setenv("CRA_TENANT_TABLE", table)
    monkeypatch.setenv("CRA_SECRET_PREFIX", prefix)
    assert tenant_live_check.main() == 2
    assert boto3.client("dynamodb", region_name="us-east-2").scan(TableName=TABLE)["Items"] == []
