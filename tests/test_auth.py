"""Customer login (auth.py, Phase 5 plan §4, step 5.1), against a fake Cognito: our own RSA keys
stand in for the user pool's JWKS, and a fake code exchange returns tokens we sign."""
import base64
import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import auth  # noqa: E402
from app import app, bundles  # noqa: E402
from safe_http import Response, SafeHttpError  # noqa: E402

POOL = "us-east-2_TestPool1"
CLIENT = "testclient0123456789"
DOMAIN = "team22-cra-test.auth.us-east-2.amazoncognito.com"
CONFIG = auth.CognitoConfig(POOL, CLIENT, DOMAIN)
ISSUER = f"https://cognito-idp.us-east-2.amazonaws.com/{POOL}"
SUB = "4b7e1c2a-0000-4000-8000-00000000abcd"
BASE = "https://team22.example.test"
CLIENT_SECRET = "cognito-client-secret-value-xyz"  # nosec B105  # a fake for tests


def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY, OTHER_KEY = new_key(), new_key()


def jwk_of(key, kid):
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "alg": "RS256", "use": "sig"}


def make_token(token_use="access", key=KEY, kid="k1", alg="RS256", drop=(), **claims):
    now = int(time.time())
    body = {"iss": ISSUER, "sub": SUB, "token_use": token_use, "iat": now, "exp": now + 3600}
    if token_use == "access":
        body["client_id"] = CLIENT
    else:
        body.update(aud=CLIENT, email="person@example.test", email_verified=True)
    body.update(claims)
    for name in drop:
        body.pop(name, None)
    return jwt.encode(body, key, algorithm=alg, headers={"kid": kid})


class FakeJwks:
    def __init__(self, *jwks):
        self.keys = list(jwks)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return {"keys": self.keys}


class FakeCognito:
    """Stands in for CognitoClient: checks the PKCE verifier against the challenge from /auth/login."""

    def __init__(self):
        self.challenge = None
        self.id_token = None      # a token, or a callable(nonce) -> token
        self.fail = False
        self.calls = []

    def exchange_code(self, code, redirect_uri, code_verifier):
        self.calls.append((code, redirect_uri))
        if self.fail:
            raise auth.TokenError("exchanging the sign-in code: HTTP 400")
        digest = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        assert digest == self.challenge
        return self.id_token


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


@pytest.fixture
def accounts():
    """Turns accounts on for the real app, with a fake Cognito, and back off afterwards."""
    jwks = FakeJwks(jwk_of(KEY, "k1"))
    cognito = FakeCognito()
    clock = Clock()
    state = auth.AuthState(CONFIG, auth.TokenVerifier(CONFIG, jwks), cognito, clock=clock)
    old_key = app.secret_key
    app.secret_key = "test-secret-key"  # nosec B105
    app.extensions["cra_auth"] = state
    bundles.clear()
    try:
        yield state
    finally:
        app.extensions.pop("cra_auth", None)
        app.secret_key = old_key


def client():
    return app.test_client()


def query_of(location):
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def start_login(c, state, page="/auth/login"):
    response = c.get(page, base_url=BASE)
    assert response.status_code == 302
    params = query_of(response.headers["Location"])
    state.cognito.challenge = params["code_challenge"]
    state.cognito.id_token = make_token("id", nonce=params["nonce"])
    return response, params


def sign_in(c, state):
    _, params = start_login(c, state)
    response = c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE)
    assert response.status_code == 302, response.data
    return response


def session_data(c):
    with c.session_transaction(base_url=BASE) as s:
        return dict(s)


def csrf_token_from(page):
    return re.search(rb'name="csrf_token" value="([^"]+)"', page).group(1).decode()


# --- accounts off (no Cognito settings): exactly Phase 4 ---

def test_account_routes_answer_503_when_accounts_are_off():
    c = client()
    for path in ("/auth/login", "/auth/signup", "/auth/callback", "/app/"):
        assert c.get(path, base_url=BASE).status_code == 503
    assert c.post("/auth/logout", base_url=BASE).status_code == 503


def test_any_bearer_token_is_ignored_when_accounts_are_off():
    bundles.clear()
    response = client().post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"},
                             headers={"Authorization": f"Bearer {make_token(exp=1)}"})
    assert response.status_code == 202


# --- sign-in ---

def test_login_redirects_to_cognito_with_state_nonce_and_pkce(accounts):
    response = client().get("/auth/login", base_url=BASE)
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert (location.scheme, location.hostname, location.path) == ("https", DOMAIN, "/oauth2/authorize")
    params = query_of(response.headers["Location"])
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT
    assert params["redirect_uri"] == f"{BASE}/auth/callback"
    assert params["scope"] == "openid email profile"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32 and len(params["nonce"]) >= 32
    assert "client_secret" not in response.headers["Location"]


def test_signup_goes_to_cognitos_sign_up_page(accounts):
    response = client().get("/auth/signup", base_url=BASE)
    assert urlsplit(response.headers["Location"]).path == "/signup"


def test_a_full_sign_in_starts_a_session_holding_only_sub_org_expiry_and_email(accounts):
    c = client()
    response = sign_in(c, accounts)
    assert response.headers["Location"].endswith("/app/")
    assert accounts.cognito.calls == [("abc", f"{BASE}/auth/callback")]
    data = session_data(c)
    assert data["sub"] == SUB
    assert data["org_id"] is None
    assert data["exp"] == pytest.approx(accounts.clock() + 8 * 3600, abs=2)
    assert data["email"] == "person@example.test"
    assert set(data) <= {"sub", "org_id", "exp", "email", "_permanent"}
    home = c.get("/app/", base_url=BASE)
    assert home.status_code == 200
    assert b"Your organizations" in home.data
    assert b"Sign out" in home.data


def test_the_session_cookie_is_httponly_secure_and_samesite_lax(accounts):
    response = sign_in(client(), accounts)
    cookie = next(v for v in response.headers.getlist("Set-Cookie") if v.startswith("cra_session="))
    attributes = {part.strip().split("=")[0].lower() for part in cookie.split(";")}
    assert {"httponly", "secure"} <= attributes
    assert "samesite=lax" in cookie.lower()
    assert "expires" in attributes


def test_no_cognito_token_is_kept_in_the_session(accounts):
    c = client()
    sign_in(c, accounts)
    cookie = c.get_cookie("cra_session", domain="team22.example.test").value
    decoded = json.dumps(app.session_interface.get_signing_serializer(app).loads(cookie))
    assert set(json.loads(decoded)) <= {"sub", "org_id", "exp", "email", "_permanent"}
    assert "eyJ" not in decoded  # no JWT


@pytest.mark.parametrize("verified", [False, "false", None])
def test_an_unverified_email_isnt_kept(accounts, verified):
    c = client()
    _, params = start_login(c, accounts)
    extra = {} if verified is None else {"email_verified": verified}
    token = make_token("id", nonce=params["nonce"], **extra)
    if verified is None:
        token = make_token("id", nonce=params["nonce"], drop=("email_verified",))
    accounts.cognito.id_token = token
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 302
    assert "email" not in session_data(c)


@pytest.mark.parametrize("query", ["code=abc&state=wrong", "code=abc", "state={state}", "error=access_denied"])
def test_a_callback_that_doesnt_match_the_sign_in_is_refused(accounts, query):
    c = client()
    _, params = start_login(c, accounts)
    response = c.get("/auth/callback?" + query.format(state=params["state"]), base_url=BASE)
    assert response.status_code == 400
    assert "sub" not in session_data(c)
    assert accounts.cognito.calls == []
    assert b"access_denied" not in response.data


def test_a_callback_without_a_sign_in_in_progress_is_refused(accounts):
    response = client().get("/auth/callback?code=abc&state=anything", base_url=BASE)
    assert response.status_code == 400


def test_a_callback_can_be_used_only_once(accounts):
    c = client()
    _, params = start_login(c, accounts)
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 302
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 400


def test_a_failed_callback_cant_be_retried_with_the_same_state(accounts):
    c = client()
    _, params = start_login(c, accounts)
    accounts.cognito.fail = True
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 502
    accounts.cognito.fail = False
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 400
    assert len(accounts.cognito.calls) == 1


def test_a_sign_in_left_too_long_is_refused(accounts):
    c = client()
    _, params = start_login(c, accounts)
    accounts.clock.now += auth.LOGIN_WINDOW_SECONDS + 1
    assert c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE).status_code == 400


def test_a_failed_code_exchange_doesnt_sign_in(accounts):
    c = client()
    _, params = start_login(c, accounts)
    accounts.cognito.fail = True
    response = c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE)
    assert response.status_code == 502
    assert "sub" not in session_data(c)


@pytest.mark.parametrize("token", [
    lambda nonce: make_token("id", nonce="someone-elses-nonce"),
    lambda nonce: make_token("id", drop=("nonce",)),
    lambda nonce: make_token("id", nonce=nonce, aud="another-client"),
    lambda nonce: make_token("id", nonce=nonce, iss="https://cognito-idp.us-east-2.amazonaws.com/us-east-2_Other"),
    lambda nonce: make_token("id", nonce=nonce, exp=int(time.time()) - 3600),
    lambda nonce: make_token("id", nonce=nonce, key=OTHER_KEY),
    lambda nonce: make_token("access", nonce=nonce),
], ids=["other-nonce", "no-nonce", "wrong-client", "wrong-pool", "expired", "bad-signature", "access-token"])
def test_an_id_token_that_fails_any_check_doesnt_sign_in(accounts, token):
    c = client()
    _, params = start_login(c, accounts)
    accounts.cognito.id_token = token(params["nonce"])
    response = c.get(f"/auth/callback?code=abc&state={params['state']}", base_url=BASE)
    assert response.status_code == 502
    assert "sub" not in session_data(c)


# --- who a request is ---

def identity_for(headers=None, cookie_client=None):
    with app.test_request_context("/bundles", headers=headers or {}, base_url=BASE):
        result = auth.resolve_identity()
        return result, auth.g.identity


def test_no_login_is_the_tester(accounts):
    result, identity = identity_for()
    assert result is None and identity.is_tester


def test_a_valid_access_token_is_that_person(accounts):
    result, identity = identity_for({"Authorization": f"Bearer {make_token()}"})
    assert result is None
    assert (identity.kind, identity.sub, identity.via) == ("user", SUB, "bearer")


@pytest.mark.parametrize("token", [
    make_token(exp=int(time.time()) - 3600),
    make_token(client_id="another-client"),
    make_token(key=OTHER_KEY),
    make_token("id"),
    make_token("id", client_id=CLIENT),
    make_token(kid="unknown-kid"),
    make_token(drop=("exp",)),
], ids=["expired", "wrong-client", "bad-signature", "id-token", "id-token-with-client-id", "unknown-key", "no-exp"])
def test_an_invalid_access_token_from_our_pool_gets_401_not_test_data(accounts, token):
    response = client().post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"},
                             headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert token.encode() not in response.data
    assert len(bundles) == 0


def test_a_get_with_an_expired_token_also_gets_401(accounts):
    response = client().get("/bundles/00000000-0000-4000-8000-000000000000",
                            headers={"Authorization": f"Bearer {make_token(exp=int(time.time()) - 60)}"})
    assert response.status_code == 401


def test_an_unsigned_token_naming_our_pool_gets_401(accounts):
    token = jwt.encode({"iss": ISSUER, "sub": SUB, "token_use": "access", "client_id": CLIENT,
                        "iat": int(time.time()), "exp": int(time.time()) + 3600}, key=None, algorithm="none")
    result, _ = identity_for({"Authorization": f"Bearer {token}"})
    assert result is not None and result.status_code == 401


def test_an_hs256_token_signed_with_the_public_key_gets_401(accounts):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "kid": "k1"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps({"iss": ISSUER, "sub": SUB}).encode()).rstrip(b"=")
    token = (header + b"." + body + b".c2ln").decode()
    result, _ = identity_for({"Authorization": f"Bearer {token}"})
    assert result is not None and result.status_code == 401


@pytest.mark.parametrize("header", [
    "Bearer phase3-local-token",
    "Bearer not.a.jwt",
    f"Bearer {make_token(iss='https://login.example.test/other-team')}",
    "Basic dGVhbTpwYXNz",
], ids=["shared-token", "garbage", "other-issuer", "basic"])
def test_other_teams_tokens_are_treated_as_no_login(accounts, header):
    response = client().post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"},
                             headers={"Authorization": header})
    assert response.status_code == 202


def test_an_expired_session_gets_401_not_test_data(accounts):
    c = client()
    sign_in(c, accounts)
    accounts.clock.now += auth.SESSION_HOURS * 3600 + 1
    response = c.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"}, base_url=BASE)
    assert response.status_code == 401
    assert len(bundles) == 0
    assert c.get("/app/", base_url=BASE).status_code == 401


def test_a_tampered_session_cookie_gets_401(accounts):
    c = client()
    sign_in(c, accounts)
    cookie = c.get_cookie("cra_session", domain="team22.example.test").value
    c.set_cookie("cra_session", cookie[:-2] + ("AA" if not cookie.endswith("AA") else "BB"),
                 domain="team22.example.test")
    response = c.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"}, base_url=BASE)
    assert response.status_code == 401


def test_a_cookie_signed_with_another_key_gets_401(accounts):
    c = client()
    sign_in(c, accounts)
    app.secret_key = "a-different-key"  # nosec B105
    assert c.get("/health", base_url=BASE).status_code == 401


def test_an_expired_login_can_always_sign_in_again(accounts):
    c = client()
    sign_in(c, accounts)
    accounts.clock.now += auth.SESSION_HOURS * 3600 + 1
    assert c.get("/app/", base_url=BASE).status_code == 401
    sign_in(c, accounts)
    assert c.get("/app/", base_url=BASE).status_code == 200


def test_a_sign_in_in_progress_is_still_the_tester(accounts):
    c = client()
    start_login(c, accounts)
    response = c.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"}, base_url=BASE)
    assert response.status_code == 202


def test_the_home_page_sends_people_who_arent_signed_in_to_sign_in(accounts):
    response = client().get("/app/", base_url=BASE)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")


# --- sign-out and CSRF ---

def test_sign_out_needs_a_csrf_token(accounts):
    c = client()
    sign_in(c, accounts)
    response = c.post("/auth/logout", base_url=BASE, headers={"Referer": f"{BASE}/app/"})
    assert response.status_code == 400
    assert session_data(c).get("sub") == SUB


def test_sign_out_clears_the_session_and_signs_out_of_cognito(accounts):
    c = client()
    sign_in(c, accounts)
    token = csrf_token_from(c.get("/app/", base_url=BASE).data)
    response = c.post("/auth/logout", data={"csrf_token": token}, base_url=BASE,
                      headers={"Referer": f"{BASE}/app/"})
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert (location.hostname, location.path) == (DOMAIN, "/logout")
    assert query_of(response.headers["Location"]) == {"client_id": CLIENT, "logout_uri": f"{BASE}/"}
    assert "sub" not in session_data(c)
    assert c.get("/app/", base_url=BASE).status_code == 302


def test_sign_out_from_another_site_is_refused(accounts):
    c = client()
    sign_in(c, accounts)
    token = csrf_token_from(c.get("/app/", base_url=BASE).data)
    response = c.post("/auth/logout", data={"csrf_token": token}, base_url=BASE,
                      headers={"Referer": "https://evil.example.test/"})
    assert response.status_code == 400


def test_sign_out_isnt_a_get(accounts):
    assert client().get("/auth/logout", base_url=BASE).status_code == 405


# --- headers ---

def test_security_headers_are_on_every_response(accounts):
    for path in ("/health", "/auth/login", "/app/"):
        headers = client().get(path, base_url=BASE).headers
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert f"form-action 'self' https://{DOMAIN}" in headers["Content-Security-Policy"]


def test_account_pages_arent_cached(accounts):
    assert client().get("/auth/login", base_url=BASE).headers["Cache-Control"] == "no-store"


# --- the token verifier on its own ---

def test_the_verifier_fetches_keys_again_when_cognito_rotates_them():
    jwks = FakeJwks(jwk_of(KEY, "k1"))
    clock = Clock()
    verifier = auth.TokenVerifier(CONFIG, jwks, clock=clock)
    assert verifier.verify_access_token(make_token())["sub"] == SUB
    rotated = make_token(key=OTHER_KEY, kid="k2")
    with pytest.raises(auth.TokenError):
        verifier.verify_access_token(rotated)       # not published yet
    jwks.keys.append(jwk_of(OTHER_KEY, "k2"))
    with pytest.raises(auth.TokenError):
        verifier.verify_access_token(rotated)       # too soon to fetch again
    clock.now += auth.JWKS_REFETCH_SECONDS
    assert verifier.verify_access_token(rotated)["sub"] == SUB
    assert jwks.calls == 2


def test_unknown_key_ids_cant_make_us_fetch_keys_on_every_request():
    jwks = FakeJwks(jwk_of(KEY, "k1"))
    verifier = auth.TokenVerifier(CONFIG, jwks, clock=Clock())
    for n in range(20):
        with pytest.raises(auth.TokenError):
            verifier.verify_access_token(make_token(kid=f"made-up-{n}"))
    assert jwks.calls == 1


def test_a_failed_key_fetch_is_a_token_error():
    def broken():
        raise auth.TokenError("fetching the user pool's keys: HTTP 503")
    with pytest.raises(auth.TokenError):
        auth.TokenVerifier(CONFIG, broken).verify_access_token(make_token())


# --- the code exchange ---

class FakeHttp:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def request(self, method, url, headers=None, body=None):
        self.requests.append((method, url, headers, body))
        if self.error:
            raise self.error
        return self.response


def test_the_code_exchange_posts_to_cognitos_token_endpoint():
    http = FakeHttp(Response(200, {}, json.dumps({"id_token": "the-id-token", "access_token": "x"}).encode()))
    cognito = auth.CognitoClient(CONFIG, CLIENT_SECRET, http_factory=lambda: http)
    assert cognito.exchange_code("the-code", f"{BASE}/auth/callback", "the-verifier") == "the-id-token"
    method, url, headers, body = http.requests[0]
    assert (method, url) == ("POST", f"https://{DOMAIN}/oauth2/token")
    assert headers["Authorization"] == "Basic " + base64.b64encode(f"{CLIENT}:{CLIENT_SECRET}".encode()).decode()
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"
    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
    assert form == {"grant_type": "authorization_code", "client_id": CLIENT, "code": "the-code",
                    "redirect_uri": f"{BASE}/auth/callback", "code_verifier": "the-verifier"}
    assert CLIENT_SECRET not in repr(cognito)


@pytest.mark.parametrize("http", [
    FakeHttp(Response(400, {}, b'{"error": "invalid_grant"}')),
    FakeHttp(Response(200, {}, b"not json")),
    FakeHttp(Response(200, {}, b'{"access_token": "only"}')),
    FakeHttp(error=SafeHttpError("timeout", "the request timed out")),
], ids=["refused", "not-json", "no-id-token", "timeout"])
def test_a_failed_code_exchange_is_a_token_error_without_the_secret(http):
    cognito = auth.CognitoClient(CONFIG, CLIENT_SECRET, http_factory=lambda: http)
    with pytest.raises(auth.TokenError) as error:
        cognito.exchange_code("the-code", f"{BASE}/auth/callback", "v")
    assert CLIENT_SECRET not in str(error.value)
    assert "invalid_grant" not in str(error.value)


# --- configuration ---

def write_secrets(tmp_path, app_secrets):
    path = tmp_path / "app.secrets.json"
    path.write_text(json.dumps({"app": app_secrets}))
    path.chmod(0o600)
    return str(path)


ENV = {"CRA_COGNITO_POOL_ID": POOL, "CRA_COGNITO_CLIENT_ID": CLIENT, "CRA_COGNITO_DOMAIN": DOMAIN,
       "CRA_TENANT_TABLE": "team22-cra-tenants-test"}


def test_accounts_are_off_without_cognito_settings():
    assert auth.load_from_env({}) is None


def test_accounts_turn_on_with_settings_and_secrets(tmp_path):
    path = write_secrets(tmp_path, {"flask-secret-key": "k" * 40, "cognito-client-secret": CLIENT_SECRET})
    loaded = auth.load_from_env({**ENV, "CRA_LOCAL_SECRETS": path})
    assert loaded is not None
    state, secret_key = loaded
    assert secret_key == "k" * 40
    assert state.config == CONFIG
    assert state.accounts.store.table == "team22-cra-tenants-test"


@pytest.mark.parametrize("env", [
    {"CRA_COGNITO_POOL_ID": POOL},
    {**ENV, "CRA_COGNITO_POOL_ID": "not a pool"},
    {**ENV, "CRA_COGNITO_DOMAIN": "evil.example.test"},
    ENV,  # no secret store
    {k: v for k, v in ENV.items() if k != "CRA_TENANT_TABLE"},
], ids=["partial", "bad-pool", "bad-domain", "no-secrets", "no-table"])
def test_bad_settings_turn_accounts_off_instead_of_crashing(env):
    assert auth.load_from_env(env) is None


def test_missing_app_secrets_turn_accounts_off(tmp_path):
    path = write_secrets(tmp_path, {"flask-secret-key": "k" * 40})
    assert auth.load_from_env({**ENV, "CRA_LOCAL_SECRETS": path}) is None


def test_the_config_derives_the_issuer_and_keys_url():
    assert CONFIG.issuer == ISSUER
    assert CONFIG.jwks_url == f"{ISSUER}/.well-known/jwks.json"
