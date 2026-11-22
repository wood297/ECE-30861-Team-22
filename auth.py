"""Customer login with Amazon Cognito (Phase 5 plan, §4, step 5.1).

Browsers sign in through Cognito's managed login pages with the authorization-code flow
(confidential client, plus PKCE, state and nonce):

    GET  /auth/login     -> Cognito's sign-in page        (/auth/signup for its sign-up page)
    GET  /auth/callback  <- Cognito sends the person back; we exchange the code and check the ID token
    POST /auth/logout    -> clears our session, then Cognito's

After the ID token checks out, the session cookie holds only `sub`, the active `org_id`, an
expiry (8 hours) and, if Cognito has verified it, the person's email, which is what invites are
matched against (step 5.2). No Cognito token is kept. API callers send a Cognito access token as
`Authorization: Bearer <token>` instead.

Who a request is (`g.identity`):
- No login at all: the read-only `pod-test` organization with the `tester` role (§7.2), exactly
  like Phase 4. This is how the class test suite reaches the system.
- A valid session or access token: that person.
- A session cookie or a token from *our* user pool that is invalid or expired: 401. It never falls
  back to `pod-test`, so a customer whose login lapsed can't silently receive test data.

A bearer token that isn't a JWT from our pool is treated as no login. Other teams' testers forward
their own shared token to every system (docs/phase-4-cross-team-testing.md, item 7), and we must
keep answering them as before; such a token can't be a customer's login, so ignoring it can't hand
a customer test data.

Without the Cognito settings (CRA_COGNITO_POOL_ID, CRA_COGNITO_CLIENT_ID, CRA_COGNITO_DOMAIN), the
tenant table (CRA_TENANT_TABLE) or the app secrets, accounts are off: /auth/* and /app/* answer 503
and every request is the tester, as today. That's how local runs and CI work.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

import jwt
from flask import Blueprint, Flask, current_app, g, jsonify, redirect, render_template, request, session, url_for

from accounts import Accounts, CognitoDirectory, NoDirectory, UserDirectory
from flask.sessions import SecureCookieSessionInterface
from flask_wtf.csrf import CSRFError, CSRFProtect

from safe_http import SafeHttpClient, SafeHttpError
from org_sources import OrgSources, TokenCheck, check_token
from secret_store import InMemorySecretStore, LocalFileSecretStore, SecretStore, SecretStoreError, SsmSecretStore
from tenant_store import DynamoTenantStore, InMemoryTenantStore

log = logging.getLogger(__name__)

SESSION_HOURS = 8
# How long someone may take on Cognito's pages between /auth/login and the callback
LOGIN_WINDOW_SECONDS = 10 * 60
# Clock difference tolerated between Cognito and us when checking exp and iat
LEEWAY_SECONDS = 30
# An unknown key ID makes us fetch the pool's keys again (Cognito rotates them), at most this often
JWKS_REFETCH_SECONDS = 60
SESSION_COOKIE = "cra_session"

_POOL_ID = re.compile(r"[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]{1,55}")
_CLIENT_ID = re.compile(r"[a-z0-9]{1,128}")
_DOMAIN = re.compile(r"[a-z0-9-]{1,63}\.auth\.[a-z]{2}-[a-z]+-\d\.amazoncognito\.com")


class TokenError(Exception):
    """A token that must not be accepted. The message is safe to log: it never holds the token."""


@dataclass(frozen=True)
class CognitoConfig:
    pool_id: str
    client_id: str
    domain: str  # managed login host, e.g. team22-cra.auth.us-east-2.amazoncognito.com

    def __post_init__(self):
        if not _POOL_ID.fullmatch(self.pool_id):
            raise ValueError("CRA_COGNITO_POOL_ID doesn't look like a user pool ID")
        if not _CLIENT_ID.fullmatch(self.client_id):
            raise ValueError("CRA_COGNITO_CLIENT_ID doesn't look like an app client ID")
        if not _DOMAIN.fullmatch(self.domain):
            raise ValueError("CRA_COGNITO_DOMAIN must be the <prefix>.auth.<region>.amazoncognito.com host")

    @property
    def region(self) -> str:
        return self.pool_id.split("_", 1)[0]

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "CognitoConfig | None":
        values = [env.get(name, "").strip() for name in
                  ("CRA_COGNITO_POOL_ID", "CRA_COGNITO_CLIENT_ID", "CRA_COGNITO_DOMAIN")]
        if not any(values):
            return None
        if not all(values):
            raise ValueError("set all of CRA_COGNITO_POOL_ID, CRA_COGNITO_CLIENT_ID and CRA_COGNITO_DOMAIN, or none")
        return cls(*values)


def _https_json(client: SafeHttpClient, method: str, url: str, what: str, **kwargs: Any) -> dict:
    try:
        response = client.request(method, url, **kwargs)
    except SafeHttpError as error:
        raise TokenError(f"{what}: {error}") from None
    if response.status != 200:
        raise TokenError(f"{what}: HTTP {response.status}")
    try:
        data = json.loads(response.body)
    except ValueError:
        raise TokenError(f"{what}: the answer isn't JSON") from None
    if not isinstance(data, dict):
        raise TokenError(f"{what}: the answer isn't a JSON object")
    return data


class TokenVerifier:
    """Checks Cognito ID and access tokens against the pool's public keys (§4)."""

    def __init__(self, config: CognitoConfig, fetch_jwks: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self._fetch_jwks = fetch_jwks or self._fetch_jwks_https
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    def _fetch_jwks_https(self) -> dict:
        client = SafeHttpClient([f"cognito-idp.{self.config.region}.amazonaws.com"], max_bytes=64 * 1024)
        return _https_json(client, "GET", self.config.jwks_url, "fetching the user pool's keys")

    def _key(self, kid: str) -> Any:
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            now = self._clock()
            if self._fetched_at is not None and now - self._fetched_at < JWKS_REFETCH_SECONDS:
                raise TokenError("the token was signed with an unknown key")
            self._fetched_at = now
            keys = {}
            for jwk in self._fetch_jwks().get("keys") or []:
                if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and isinstance(jwk.get("kid"), str):
                    try:
                        keys[jwk["kid"]] = jwt.PyJWK(jwk, algorithm="RS256").key
                    except jwt.PyJWTError:
                        continue
            self._keys = keys
            if kid not in keys:
                raise TokenError("the token was signed with an unknown key")
            return keys[kid]

    def is_ours(self, token: str) -> bool:
        """Whether a token claims to come from our pool. Nothing is checked yet, so this only
        decides whether to check it (and 401 if it fails) or treat it as no login."""
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return False
        return isinstance(claims, dict) and claims.get("iss") == self.config.issuer

    def _verify(self, token: str, use: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenError("the token is malformed") from None
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise TokenError("the token isn't signed the way Cognito signs tokens")
        key = self._key(header["kid"])
        try:
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.config.issuer,
                audience=self.config.client_id if use == "id" else None,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "sub", "token_use"],
                         "verify_aud": use == "id"})
        except jwt.ExpiredSignatureError:
            raise TokenError("the token has expired") from None
        except jwt.InvalidIssuerError:
            raise TokenError("the token is from another user pool") from None
        except jwt.InvalidAudienceError:
            raise TokenError("the token is for another app client") from None
        except jwt.PyJWTError as error:
            raise TokenError(f"the token is invalid ({type(error).__name__})") from None
        if claims.get("token_use") != use:
            raise TokenError(f"expected an {use} token")
        if use == "access" and claims.get("client_id") != self.config.client_id:
            raise TokenError("the token is for another app client")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise TokenError("the token has no subject")
        return claims

    def verify_id_token(self, token: str, nonce: str) -> dict:
        claims = self._verify(token, "id")
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise TokenError("the ID token wasn't issued for this sign-in")
        return claims

    def verify_access_token(self, token: str) -> dict:
        return self._verify(token, "access")


class CognitoClient:
    """The part of Cognito's OAuth endpoints we call: exchanging a sign-in code for tokens."""

    def __init__(self, config: CognitoConfig, client_secret: str,
                 http_factory: Callable[[], SafeHttpClient] | None = None):
        self.config = config
        self._basic = base64.b64encode(f"{config.client_id}:{client_secret}".encode()).decode()
        self._http_factory = http_factory or (lambda: SafeHttpClient([config.domain], max_bytes=64 * 1024))

    def __repr__(self) -> str:
        return f"CognitoClient({self.config.domain})"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> str:
        """The ID token for a sign-in code. Raises TokenError."""
        body = urlencode({"grant_type": "authorization_code", "client_id": self.config.client_id,
                          "code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        data = _https_json(self._http_factory(), "POST", f"https://{self.config.domain}/oauth2/token",
                           "exchanging the sign-in code",
                           headers={"Authorization": f"Basic {self._basic}",
                                    "Content-Type": "application/x-www-form-urlencoded"},
                           body=body.encode())
        if not isinstance(data.get("id_token"), str):
            raise TokenError("exchanging the sign-in code: no ID token in the answer")
        return data["id_token"]


@dataclass
class AuthState:
    """Everything account features need. Absent from app.extensions when accounts are off."""
    config: CognitoConfig
    verifier: TokenVerifier
    cognito: CognitoClient
    clock: Callable[[], float] = time.time
    accounts: Accounts = field(default_factory=lambda: Accounts(InMemoryTenantStore()))
    directory: UserDirectory = field(default_factory=NoDirectory)
    # Where companies' connection tokens are written and read (step 5.3)
    secrets: SecretStore = field(default_factory=InMemorySecretStore)
    token_check: TokenCheck = check_token

    @property
    def sources(self) -> OrgSources:
        return OrgSources(self.accounts, self.secrets, self.token_check)


@dataclass(frozen=True)
class Identity:
    kind: str                   # "tester" (no login) or "user"
    sub: str | None = None
    org_id: str | None = None   # the active organization; chosen in 5.2
    via: str | None = None      # "session" or "bearer"

    @property
    def is_tester(self) -> bool:
        return self.kind == "tester"


TESTER = Identity("tester")


class _FlaggingSessionInterface(SecureCookieSessionInterface):
    """Flask's signed-cookie sessions, but a cookie that fails its signature or age check is marked
    `invalid` instead of silently becoming an empty session (which would look like no login)."""

    def open_session(self, app, request):
        opened = super().open_session(app, request)
        if opened is not None and not opened and request.cookies.get(self.get_cookie_name(app)):
            opened.invalid = True  # type: ignore[attr-defined]
        return opened


def load_from_env(env: Mapping[str, str] = os.environ) -> tuple[AuthState, str] | None:
    """(the auth state, the Flask secret key), or None when accounts are off. Never raises: a
    misconfiguration turns accounts off and is logged, so it can't take the bundle API down."""
    try:
        config = CognitoConfig.from_env(env)
    except ValueError as error:
        log.error("accounts are off: %s", error)
        return None
    if config is None:
        return None
    table = env.get("CRA_TENANT_TABLE", "").strip()
    if not table:
        log.error("accounts are off: CRA_TENANT_TABLE is not set")
        return None
    try:
        store: SecretStore
        if env.get("CRA_LOCAL_SECRETS"):
            store = LocalFileSecretStore(env["CRA_LOCAL_SECRETS"])
        elif env.get("CRA_SECRET_PREFIX"):
            store = SsmSecretStore(env["CRA_SECRET_PREFIX"], region=config.region)
        else:
            log.error("accounts are off: CRA_SECRET_PREFIX (or CRA_LOCAL_SECRETS) is not set")
            return None
        secret_key = store.get_app_secret("flask-secret-key").reveal()
        client_secret = store.get_app_secret("cognito-client-secret").reveal()
    except (SecretStoreError, ValueError, OSError) as error:
        log.error("accounts are off: couldn't read the app secrets (%s)", type(error).__name__)
        return None
    except Exception as error:  # boto3 raises botocore errors for missing credentials and the like
        log.error("accounts are off: couldn't read the app secrets (%s)", type(error).__name__)
        return None
    try:
        accounts = Accounts(DynamoTenantStore(table, region=config.region))
    except Exception as error:
        log.error("accounts are off: couldn't open the tenant table (%s)", type(error).__name__)
        return None
    return AuthState(config, TokenVerifier(config), CognitoClient(config, client_secret),
                     accounts=accounts, directory=CognitoDirectory(config.pool_id, config.region),
                     secrets=store), secret_key


csrf = CSRFProtect()
bp = Blueprint("auth", __name__)


def _state() -> AuthState | None:
    return current_app.extensions.get("cra_auth")


# Blueprints whose pages are HTML for people, not JSON for programs
PAGE_BLUEPRINTS = {"auth", "account"}


def _wants_html() -> bool:
    return request.blueprint in PAGE_BLUEPRINTS


def _unauthorized(reason: str, bearer: bool = False):
    if _wants_html():
        response = current_app.make_response(
            (render_template("account/message.html", title="Please sign in again",
                             message="Your sign-in has expired or isn't valid.", sign_in=True), 401))
    else:
        response = jsonify({"error": f"Unauthorized: {reason}. Sign in again, or send no credentials "
                                     "to use the public test data."})
        response.status_code = 401
    if bearer:
        response.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return response


def message_page(status: int, title: str, message: str, sign_in: bool = False):
    return render_template("account/message.html", title=title, message=message, sign_in=sign_in), status


# Endpoints that work whatever state the session is in, so an expired login can always start over
_AUTH_FLOW = {"auth.login", "auth.signup", "auth.callback", "auth.logout"}


def resolve_identity():
    """Before every request of the system app: set g.identity, or answer 401."""
    g.identity = TESTER
    state = _state()
    if state is None:
        return None
    if request.endpoint in _AUTH_FLOW:
        return None

    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token and state.verifier.is_ours(token):
        try:
            claims = state.verifier.verify_access_token(token)
        except TokenError as error:
            log.info("refused a bearer token: %s", error)
            return _unauthorized(str(error), bearer=True)
        g.identity = Identity("user", sub=claims["sub"], via="bearer")
        return None

    if getattr(session, "invalid", False):
        return _unauthorized("the session cookie is invalid or expired")
    if "sub" in session:
        expires = session.get("exp")
        if not isinstance(expires, (int, float)) or expires <= state.clock():
            return _unauthorized("the session has expired")
        g.identity = Identity("user", sub=session["sub"], org_id=session.get("org_id"), via="session")
    return None


@bp.before_request
def _accounts_on():
    if _state() is None:
        return message_page(503, "Accounts aren't available", "Accounts aren't set up on this server.")
    if request.method == "POST":
        csrf.protect()
    return None


def _code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _start_login(page: str):
    state = _state()
    assert state is not None  # nosec B101  # _accounts_on answered 503 otherwise
    pending: dict[str, Any] = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
               "verifier": secrets.token_urlsafe(64), "started": int(state.clock())}
    # Starting a sign-in replaces whatever the session held, including an expired login
    session.clear()
    session["login"] = pending
    query = urlencode({"response_type": "code", "client_id": state.config.client_id,
                       "redirect_uri": url_for("auth.callback", _external=True),
                       "scope": "openid email profile", "state": pending["state"], "nonce": pending["nonce"],
                       "code_challenge": _code_challenge(pending["verifier"]), "code_challenge_method": "S256"})
    return redirect(f"https://{state.config.domain}/{page}?{query}")


@bp.route("/auth/login", methods=["GET"])
def login():
    return _start_login("oauth2/authorize")


@bp.route("/auth/signup", methods=["GET"])
def signup():
    return _start_login("signup")


@bp.route("/auth/callback", methods=["GET"])
def callback():
    state = _state()
    assert state is not None  # nosec B101
    pending = session.pop("login", None)
    if not isinstance(pending, dict) or state.clock() - pending.get("started", 0) > LOGIN_WINDOW_SECONDS:
        return message_page(400, "Sign-in expired", "That sign-in took too long or was already used. "
                                                "Please start again.", sign_in=True)
    if request.args.get("error"):
        # Cancelled or refused on Cognito's side. The value isn't shown: it comes from the URL
        return message_page(400, "Sign-in didn't finish", "Sign-in was cancelled or refused.", sign_in=True)
    returned_state, code = request.args.get("state", ""), request.args.get("code", "")
    if not returned_state or not hmac.compare_digest(returned_state, pending.get("state", "")) or not code:
        return message_page(400, "Sign-in didn't finish", "That sign-in link isn't valid for this browser. "
                                                      "Please start again.", sign_in=True)
    try:
        id_token = state.cognito.exchange_code(code, url_for("auth.callback", _external=True), pending["verifier"])
        claims = state.verifier.verify_id_token(id_token, pending["nonce"])
    except TokenError as error:
        log.warning("sign-in failed: %s", error)
        return message_page(502, "Sign-in didn't finish", "We couldn't confirm your sign-in with Cognito. "
                                                      "Please try again.", sign_in=True)
    session.clear()
    session.permanent = True
    session["sub"] = claims["sub"]
    session["org_id"] = None
    session["exp"] = int(state.clock()) + SESSION_HOURS * 3600
    if claims.get("email_verified") in (True, "true") and isinstance(claims.get("email"), str):
        session["email"] = claims["email"].strip().lower()
    return redirect(url_for("account.home"))


@bp.route("/auth/logout", methods=["POST"])
def logout():
    state = _state()
    assert state is not None  # nosec B101
    session.clear()
    query = urlencode({"client_id": state.config.client_id, "logout_uri": url_for("index", _external=True)})
    return redirect(f"https://{state.config.domain}/logout?{query}")


def _security_headers(response):
    headers = response.headers
    state = _state()
    form_targets = "'self'" + (f" https://{state.config.domain}" if state else "")
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    # same-origin, not no-referrer: CSRF checks on HTTPS compare the Referer with our host
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Content-Security-Policy",
                       "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
                       f"form-action {form_targets}; frame-ancestors 'none'; base-uri 'none'")
    if request.blueprint in PAGE_BLUEPRINTS:
        headers["Cache-Control"] = "no-store"
    return response


def init_app(app: Flask, loaded: tuple[AuthState, str] | None) -> None:
    """Wire login into the system app. `loaded` comes from load_from_env(); None keeps accounts off."""
    app.session_interface = _FlaggingSessionInterface()
    app.config.update(
        SESSION_COOKIE_NAME=SESSION_COOKIE,
        SESSION_COOKIE_HTTPONLY=True,
        # Only local development against the -dev pool runs over http://localhost
        SESSION_COOKIE_SECURE=os.environ.get("CRA_INSECURE_COOKIES") != "1",
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
        # Only the account pages check CSRF tokens. The JSON API is exempt: browsers can't send it
        # cross-site with cookies (SameSite=Lax, and get_json() needs application/json)
        WTF_CSRF_CHECK_DEFAULT=False,
        # The token is tied to the session, which already expires
        WTF_CSRF_TIME_LIMIT=None,
    )
    if loaded is not None:
        state, secret_key = loaded
        app.secret_key = secret_key
        app.extensions["cra_auth"] = state
    csrf.init_app(app)
    app.register_blueprint(bp)
    from account_pages import bp as account_bp
    import source_pages  # noqa: F401  # adds the connection, product and bundle pages to account_bp
    app.register_blueprint(account_bp)
    app.before_request(resolve_identity)
    app.after_request(_security_headers)

    @app.errorhandler(CSRFError)
    def _csrf_failed(error):
        return message_page(400, "Form expired", "That form has expired. Go back, reload the page and try again.")
