"""The connector kit itself: fields, results, the run_fetch safety net, masking, local secrets."""
import base64
import json
import logging
import os
import uuid

import pytest

from connectors import REGISTRY, make_context
from connectors.base import (AUTH_FAILED, LIST, MASK, MAX_DETAIL_CHARS, RETRIEVED, SECRET, SOURCE_ERROR, TEXT,
                             UNAVAILABLE, URL, BaseConnector, FetchContext, FetchResult, Field, SettingsError,
                             check_values, fill_ref, result_for_status, run_fetch)
from connectors.try_fetch import main as try_fetch
from safe_http import SafeHttpClient, SafeHttpError
from secret_store import InMemorySecretStore, LocalFileSecretStore, Secret, SecretStoreError
from tenant_store import Connection

TOKEN = "base-test-token-1234567890"
ORG, CONN = str(uuid.uuid4()), str(uuid.uuid4())


class Scripted(BaseConnector):
    """A connector whose fetch() does whatever the test says."""
    type = "scripted"
    display_name = "Scripted"
    settings_fields = (Field("email", "Email"), Field("token", "Token", kind=SECRET))
    ref_fields = (Field("paths", "Paths", kind=LIST),)

    def __init__(self, behave):
        self.behave = behave

    def allowed_hosts(self, settings):
        return ["example.com"]

    def fetch(self, conn, ref, ctx):
        return self.behave(ctx)


def run(behave, caplog=None):
    secrets = InMemorySecretStore()
    secrets.put_connection_secret(ORG, CONN, TOKEN)
    connector = Scripted(behave)
    conn = Connection(ORG, CONN, "scripted", {"email": "bot@acme.example"})
    if caplog is not None:
        caplog.set_level(logging.DEBUG)
    return run_fetch(connector, conn, {"paths": ["a"]}, make_context(connector, conn, secrets))


# Fields and values

def test_values_are_checked_against_their_fields():
    fields = [Field("site", "Site", pattern=r"[a-z0-9-]+\.atlassian\.net"), Field("url", "URL", kind=URL),
              Field("paths", "Paths", kind=LIST), Field("note", "Note", required=False),
              Field("token", "Token", kind=SECRET)]
    clean = check_values(fields, {"site": " acme.atlassian.net ", "url": "https://x.example/a/", "paths": "a\n\nb\n"})
    assert clean == {"site": "acme.atlassian.net", "url": "https://x.example/a", "paths": ["a", "b"]}
    for bad in ({"url": "https://x.example", "paths": ["a"]},                            # site missing
                {**clean, "site": "evil.example"}, {**clean, "url": "http://x.example"},
                {**clean, "token": "smuggled into settings"}, {**clean, "paths": []},
                {**clean, "paths": ["a"] * 6}, {**clean, "note": "line\nbreak"}, {**clean, "site": 5}):
        with pytest.raises(SettingsError):
            check_values(fields, bad)


def test_placeholders_are_only_allowed_in_refs_and_only_known_ones():
    fields = [Field("path", "Path", pattern=r"[a-z0-9/._-]+")]
    assert check_values(fields, {"path": "{release}/sbom.md"}, allow_placeholders=True)
    for value, allow in (("{release}/sbom.md", False), ("{org}/sbom.md", True), ("{}/x", True)):
        with pytest.raises(SettingsError):
            check_values(fields, {"path": value}, allow_placeholders=allow)


def test_fill_ref_replaces_placeholders_everywhere():
    ref = {"paths": ["{product}/{release}/sbom.json", "x"], "tag": "v{release}", "build": 7}
    assert fill_ref(ref, "prd-88", "5.0") == {"paths": ["prd-88/5.0/sbom.json", "x"], "tag": "v5.0", "build": 7}


@pytest.mark.parametrize("make", [
    lambda: Field("Bad Name", "x"), lambda: Field("ok", "x", kind="password"),
])
def test_bad_field_declarations_fail_at_import(make):
    with pytest.raises(ValueError):
        make()


# Results

@pytest.mark.parametrize("kwargs", [
    {"outcome": "MAYBE"}, {"outcome": RETRIEVED}, {"outcome": RETRIEVED, "data": b"x", "filename": "../x"},
    {"outcome": RETRIEVED, "data": "text", "filename": "x"}, {"outcome": UNAVAILABLE, "data": b"x"},
])
def test_malformed_results_are_refused(kwargs):
    with pytest.raises(ValueError):
        FetchResult(**kwargs)


def test_status_helper():
    assert result_for_status(200, "X") is None
    assert [result_for_status(s, "X").outcome for s in (401, 403, 404, 429, 500, 302, 418)] == \
        ["AUTH_FAILED", "AUTH_FAILED", "NOT_FOUND", "UNAVAILABLE", "UNAVAILABLE", "SOURCE_ERROR", "SOURCE_ERROR"]


# The run_fetch safety net

def test_a_crashing_connector_costs_one_category_and_logs_no_message(caplog):
    def crash(ctx):
        raise KeyError(f"token was {ctx.secret().reveal()}")
    result = run(crash, caplog)
    assert result.outcome == SOURCE_ERROR and "unexpectedly" in result.detail
    assert "KeyError" in caplog.text and TOKEN not in caplog.text


def test_a_connector_returning_something_else_is_a_source_error():
    assert run(lambda ctx: {"outcome": "RETRIEVED"}).outcome == SOURCE_ERROR


def test_uncaught_http_errors_and_missing_secrets_become_results():
    def http_error(ctx):
        raise SafeHttpError("timeout", "example.com did not answer in time.")
    assert run(http_error).outcome == UNAVAILABLE

    connector = Scripted(lambda ctx: FetchResult.retrieved(ctx.secret().reveal().encode(), "x"))
    conn = Connection(ORG, CONN, "scripted", {"email": "bot@acme.example"})
    assert run_fetch(connector, conn, {}, make_context(connector, conn, InMemorySecretStore())).outcome == AUTH_FAILED
    assert run_fetch(connector, conn, {}, make_context(connector, conn, None)).outcome == AUTH_FAILED


def test_secrets_in_details_and_logs_are_masked_in_every_form(caplog):
    basic = base64.b64encode(f"bot@acme.example:{TOKEN}".encode()).decode()

    def leaky(ctx):
        token = ctx.secret().reveal()
        ctx.masker.add(basic)
        ctx.log.info("sending %s and Basic %s", token, basic)
        ctx.log.warning({"token": ctx.secret()})
        return FetchResult.auth_failed(f"rejected {token} / {base64.b64encode(token.encode()).decode()} / {basic}")
    result = run(leaky, caplog)
    assert result.outcome == AUTH_FAILED
    assert TOKEN not in result.detail and basic not in result.detail and MASK in result.detail
    assert TOKEN not in caplog.text and basic not in caplog.text


def test_details_are_one_short_line():
    result = run(lambda ctx: FetchResult.unavailable("line one\nline two\x1b[31m " + "x" * 1000))
    assert "\n" not in result.detail and "\x1b" not in result.detail
    assert len(result.detail) <= MAX_DETAIL_CHARS


def test_the_context_reads_the_secret_only_when_asked():
    calls = []
    ctx = FetchContext(SafeHttpClient([]), lambda: calls.append(1) or Secret(TOKEN))
    assert calls == []
    assert ctx.secret().reveal() == TOKEN and ctx.secret().reveal() == TOKEN
    assert calls == [1]


def test_the_contexts_http_client_is_limited_to_the_allowed_hosts():
    connector = Scripted(None)
    ctx = make_context(connector, Connection(ORG, CONN, "scripted", {}), None)
    with pytest.raises(SafeHttpError) as error:
        ctx.http.get("https://elsewhere.example/")
    assert error.value.code == "host_not_allowed"


def test_the_registry_holds_valid_connectors():
    assert "fixtures" in REGISTRY
    for name, connector in REGISTRY.items():
        assert connector.type == name


# Local secrets

def write_secrets(tmp_path, data, mode=0o600):
    path = tmp_path / "dev.secrets.json"
    path.write_text(json.dumps(data))
    os.chmod(path, mode)
    return str(path)


def test_local_secrets_are_read_from_a_private_file(tmp_path):
    store = LocalFileSecretStore(write_secrets(tmp_path, {"connections": {CONN: TOKEN}, "app": {"github-app": "k"}}))
    assert store.get_connection_secret(ORG, CONN).reveal() == TOKEN
    assert store.get_app_secret("github-app").reveal() == "k"
    with pytest.raises(SecretStoreError):
        store.put_connection_secret(ORG, CONN, "x")


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_local_secrets_readable_by_others_are_refused(tmp_path):
    with pytest.raises(SecretStoreError):
        LocalFileSecretStore(write_secrets(tmp_path, {"connections": {}}, mode=0o644))


def test_local_secrets_reject_bad_ids(tmp_path):
    with pytest.raises(ValueError):
        LocalFileSecretStore(write_secrets(tmp_path, {"connections": {"../app": TOKEN}}))


# The local runner

def test_try_fetch_runs_a_connector_locally(tmp_path, capsys):
    spec = tmp_path / "fixtures.local.json"
    spec.write_text(json.dumps({"type": "fixtures", "ref": {"paths": ["{product}/{release}/sbom.md"]}}))
    assert try_fetch([str(spec), "--release", "v1.2", "--save", str(tmp_path / "out.md")]) == 0
    out = capsys.readouterr().out
    assert "RETRIEVED" in out and "sha256:" in out and (tmp_path / "out.md").read_bytes()

    spec.write_text(json.dumps({"type": "fixtures", "ref": {"paths": ["{product}/{release}/none.md"]}}))
    assert try_fetch([str(spec), "--release", "v1.2"]) == 1
    spec.write_text(json.dumps({"type": "nope"}))
    assert try_fetch([str(spec)]) == 2


def test_text_fields_have_sensible_defaults():
    assert Field("x", "X").kind == TEXT and not Field("x", "X").secret
