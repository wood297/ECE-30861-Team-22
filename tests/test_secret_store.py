"""SecretStore contract (Phase 5 plan, step 1.3), run against both implementations on moto."""
import logging
import pickle
import uuid

import boto3
import pytest
from moto import mock_aws

from secret_store import (MAX_SECRET_BYTES, InMemorySecretStore, Secret, SecretNotFound, SsmSecretStore,
                          check_prefix)

PREFIX = "/team22-cra-test"
ORG, OTHER_ORG = str(uuid.uuid4()), str(uuid.uuid4())
CONN, OTHER_CONN = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def ssm(monkeypatch):
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        yield boto3.client("ssm", region_name="us-east-2")


@pytest.fixture(params=["memory", "ssm"])
def store(request):
    if request.param == "memory":
        return InMemorySecretStore(PREFIX)
    return SsmSecretStore(PREFIX, client=request.getfixturevalue("ssm"))


def test_a_connection_secret_round_trips(store):
    path = store.put_connection_secret(ORG, CONN, "ghp_token-value")
    assert path == f"{PREFIX}/orgs/{ORG}/conn/{CONN}" == store.connection_path(ORG, CONN)
    assert store.get_connection_secret(ORG, CONN).reveal() == "ghp_token-value"


def test_putting_again_replaces_the_secret(store):
    store.put_connection_secret(ORG, CONN, "old")
    store.put_connection_secret(ORG, CONN, "new")
    assert store.get_connection_secret(ORG, CONN).reveal() == "new"


def test_a_missing_secret_is_not_found(store):
    with pytest.raises(SecretNotFound):
        store.get_connection_secret(ORG, CONN)
    with pytest.raises(SecretNotFound):
        store.get_app_secret("flask-secret-key")


def test_deleting_a_secret_and_deleting_twice(store):
    store.put_connection_secret(ORG, CONN, "token")
    store.delete_connection_secret(ORG, CONN)
    store.delete_connection_secret(ORG, CONN)
    with pytest.raises(SecretNotFound):
        store.get_connection_secret(ORG, CONN)


def test_deleting_an_organizations_secrets_leaves_other_organizations_alone(store):
    for i in range(12):   # more than SSM's 10 per delete call
        store.put_connection_secret(ORG, str(uuid.uuid4()), f"token-{i}")
    store.put_connection_secret(OTHER_ORG, OTHER_CONN, "keep me")
    store.put_app_secret("flask-secret-key", "keep me too")

    assert store.delete_org_secrets(ORG) == 12
    assert store.delete_org_secrets(ORG) == 0
    assert store.get_connection_secret(OTHER_ORG, OTHER_CONN).reveal() == "keep me"
    assert store.get_app_secret("flask-secret-key").reveal() == "keep me too"


def test_organizations_cant_read_each_others_secrets(store):
    store.put_connection_secret(ORG, CONN, "acme token")
    with pytest.raises(SecretNotFound):
        store.get_connection_secret(OTHER_ORG, CONN)


def test_app_secrets(store):
    store.put_app_secret("github-app/private-key", "-----BEGIN KEY-----")
    assert store.get_app_secret("github-app/private-key").reveal() == "-----BEGIN KEY-----"


@pytest.mark.parametrize("org_id, conn_id", [
    ("../app", CONN), (ORG, "../../app/flask-secret-key"), ("", CONN), (ORG, ORG.upper()), ("x/y", CONN),
])
def test_ids_that_could_escape_the_path_are_refused(store, org_id, conn_id):
    with pytest.raises(ValueError):
        store.put_connection_secret(org_id, conn_id, "token")
    with pytest.raises(ValueError):
        store.get_connection_secret(org_id, conn_id)


@pytest.mark.parametrize("name", ["", "../orgs", "Flask", "a//b", "a/b/c/d/e", "x" * 64])
def test_bad_app_secret_names_are_refused(store, name):
    with pytest.raises(ValueError):
        store.put_app_secret(name, "value")


@pytest.mark.parametrize("value", ["", "   ", None, "x" * (MAX_SECRET_BYTES + 1), "é" * (MAX_SECRET_BYTES // 2 + 1)])
def test_bad_secret_values_are_refused(store, value):
    with pytest.raises(ValueError):
        store.put_connection_secret(ORG, CONN, value)


def test_ssm_stores_an_encrypted_securestring(ssm):
    SsmSecretStore(PREFIX, client=ssm).put_connection_secret(ORG, CONN, "token")
    parameter = ssm.get_parameter(Name=f"{PREFIX}/orgs/{ORG}/conn/{CONN}")["Parameter"]
    assert parameter["Type"] == "SecureString"
    assert parameter["Value"] != "token"


@pytest.mark.parametrize("prefix", ["", "team22-cra", "/", "/Team22", "/a/b/c/d", "/x/"])
def test_bad_prefixes_are_refused(prefix):
    with pytest.raises(ValueError):
        check_prefix(prefix)


# The Secret wrapper

def test_a_secret_never_shows_its_value_by_accident(caplog):
    secret = Secret("super-secret-token")
    assert "super-secret-token" not in repr(secret)
    assert "super-secret-token" not in str(secret)
    assert "super-secret-token" not in f"{secret} {[secret]} {({'token': secret})}"
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("token is %s", secret)
        logging.getLogger("test").info("connection %r", {"token": secret})
    assert "super-secret-token" not in caplog.text
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    assert secret == Secret("super-secret-token") and secret != Secret("other")
