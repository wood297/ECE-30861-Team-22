# Writing a connector

A connector fetches **one file for one category from one source** (GitHub, Confluence, Jenkins). That's all it does. The code around it resolves products and releases, decides statuses, validates formats, runs Bedrock, caches results, and handles AWS, logins and organizations. You never touch any of that.

> **Branch:** the kit isn't on `main` yet. Branch from `person3-bounded-memory`, and open your PR **against `person3-bounded-memory`**, not `main`.

The contract is in [`connectors/base.py`](connectors/base.py). Read these three files first, in this order:

| File | What it shows |
|---|---|
| `connectors/fixtures.py` | The smallest complete connector (local files, no network) |
| `connectors/example_http.py` | A complete HTTP connector with a token. **Copy this one** |
| `tests/test_connector_example_http.py` | Its tests: the shared contract tests plus its own |

## 1. Write it

Create `connectors/<type>.py` with a subclass of `BaseConnector`:

```python
class ConfluenceConnector(BaseConnector):
    type = "confluence"                      # the registry key
    display_name = "Confluence"              # shown to companies and as external_source
    settings_fields = (                      # entered once per connection
        Field("site", "Site", pattern=r"[a-z0-9-]+\.atlassian\.net"),
        Field("email", "Account email"),
        Field("token", "API token", kind=SECRET),
    )
    ref_fields = (                           # pinned by each product or release
        Field("page_id", "Page ID", pattern=r"[0-9]{1,20}"),
        Field("filename", "Attachment name"),
        Field("version", "Attachment version", kind=INTEGER),
    )

    def allowed_hosts(self, settings):  ...  # every host ctx.http may reach
    def fetch(self, conn, ref, ctx):    ...  # returns a FetchResult
```

**Fields.** Kinds are `text`, `url` (https only), `integer`, `list` (candidates tried in order, one per line in a form) and `secret`. A connection has at most one `secret` field. Its value goes to SSM and never into `conn.settings`; you read it with `ctx.secret()`. A `pattern` is a full-match regex. The default `validate_settings` and `validate_ref` check the fields, so override them only to add checks (and call `super()` first).

**Placeholders.** Ref values may contain `{product}` and `{release}`, e.g. `artifacts/{product}/{release}/sbom.json`. They're filled in before `fetch()` is called, so `fetch()` only ever sees concrete values.

**What `fetch()` gets:**
- `conn.settings`: the validated settings, without the secret
- `ref`: the concrete reference
- `ctx.http`: the only way to make a request (`.get(url, headers=)`, `.request(method, url, headers=, body=)`). It returns `Response(status, headers, body)`, with lower-case header names, and raises `SafeHttpError` for anything refused or failed
- `ctx.secret()`: the token, as a `Secret`. Call `.reveal()` only when building the request
- `ctx.log`: logs with secrets masked
- `ctx.time_left()`: seconds until the bundle's deadline

**What it returns.** Use the `FetchResult` constructors:

| Constructor | When | The company sees |
|---|---|---|
| `FetchResult.retrieved(data, filename, version=)` | You have the file's original bytes. `version` is what the source says you got (commit, attachment version, build number) | The normal rules: format check, headings, Bedrock |
| `.not_found(detail)` | The pinned file, page, attachment version or build doesn't exist | `INACCESSIBLE` |
| `.auth_failed(detail)` | Credentials missing, rejected, or lacking permission | `INACCESSIBLE` |
| `.unavailable(detail)` | Timeout, rate limit, 5xx. Might work later, so it's never cached | `INACCESSIBLE` |
| `.source_error(detail)` | The source answered, but not usably: two attachments with the same name, a malformed listing, an unexpected redirect, a file over the size limit | `INACCESSIBLE` |

`result_for_http_error(error, display_name)` and `result_for_status(status, display_name)` map the usual cases, so all connectors map them the same way. Check your source's special cases first.

### The rules

The contract tests check every one of these.

1. **Never raise for a problem with the source.** Catch `SafeHttpError` and turn every status you get into a result. `run_fetch()` catches anything that escapes, but it reports it as "failed unexpectedly", which helps nobody.
2. **Use only `ctx.http`.** Don't use `requests`, `urllib` or `http.client`. `ctx.http` blocks internal addresses (the EC2 metadata service is one request away), enforces HTTPS, timeouts, size limits and the deadline, and never follows redirects.
3. **Redirects are yours to check.** A 3xx comes back as a response. Follow it only if the `location` is a host you expect, and include that host in `allowed_hosts()`. Don't send the token to a redirect target unless the source requires it.
4. **`allowed_hosts()` is as narrow as possible**: the company's site plus the API hosts you need. Never `*`, and never a whole TLD.
5. **The secret never goes in a URL, a `detail`, a log line or an exception message.** It goes in a header. If you send it in another form (e.g. a Basic header is base64 of `email:token`), call `ctx.masker.add(that_form)` so it's masked too.
6. **`detail` is one short line you write yourself**, e.g. "Confluence has no attachment risk_assessment.md on page 622599." Never paste in an upstream response body.
7. **Only a confirmed absence moves on to the next candidate.** A 404 means try the next path. A 401, 5xx or timeout means stop and return that result. Otherwise an outage looks like "file missing".
8. **A ref pins something that doesn't change**: a commit SHA, an attachment *version*, a build number. If a re-upload could change what a ref returns, add a version field.

## 2. Register it

Add one line to `REGISTRY` in [`connectors/__init__.py`](connectors/__init__.py):

```python
"confluence": ConfluenceConnector(),
```

## 3. Test it

Create `tests/test_connector_<type>.py` and subclass `HttpConnectorContract`. You describe how your source answers when the file exists (`serve_file`) and when it doesn't (`serve_missing`). The contract then runs about 20 tests on your connector, using the real `safe_http` checks against a fake network:
- it retrieves the file, and `serve_missing` gives NOT_FOUND
- 401/403 give AUTH_FAILED
- 429/5xx, timeouts and refused connections give UNAVAILABLE, and an expired deadline sends nothing
- garbage bodies never raise, and oversized answers are never retrieved
- redirects to the metadata service, localhost or another host are never followed
- a host that resolves to an internal address is refused
- the secret appears in no URL, no result and no log line

```python
class TestConfluenceContract(HttpConnectorContract):
    connector = ConfluenceConnector()
    settings = {"site": "acme.atlassian.net", "email": "bot@acme.example"}
    ref = {"page_id": "622599", "filename": "risk_assessment.md", "version": "2"}
    expected_filename = "risk_assessment.md"

    def serve_file(self, request):
        if "/attachments" in request.target:
            return FakeResponse.json({"results": [...], "_links": {}})
        ...
    def serve_missing(self, request):
        return FakeResponse.json({"results": [], "_links": {}})
```

`request` has `method`, `host`, `target` (path and query), `headers` and `body`. The fake token is `connector_contract.SECRET`.

Add your own tests next to the contract tests for whatever is specific to your source: pagination, ambiguous matches, a redirect you do follow, version pinning. Run them with:

```
pytest tests/test_connector_<type>.py
```

## 4. Run it against the real source

You need no AWS access. Put your token in a local secrets file and the connection in a local connection file. Both patterns are in `.gitignore`.

```
# confluence.secrets.json   (chmod 600 confluence.secrets.json)
{"connections": {"6f1c1d2e-0000-4000-8000-000000000001": "<your token>"}}

# confluence.local.json
{"type": "confluence",
 "conn_id": "6f1c1d2e-0000-4000-8000-000000000001",
 "settings": {"site": "whcora.atlassian.net", "email": "you@example.com"},
 "ref": {"page_id": "622599", "filename": "risk_assessment.md", "version": "2"}}
```

```
CRA_LOCAL_SECRETS=confluence.secrets.json python -m connectors.try_fetch confluence.local.json \
    --product prd-88 --release v5.0 -v
```

It runs the same validation, `safe_http` limits and `run_fetch()` safety net as the server, then prints the outcome, filename, size and SHA-256. It never prints the file or the token; `--save out.md` writes the file. For a Jenkins on your LAN, add `--private-network 192.168.1.0/24` (the server only allows this from its own configuration, never from a company's settings).

Also add a **live check script** (like `github_live_check.py`) that fetches the pinned test file and compares its SHA-256 with the expected bytes. It runs outside pytest.

## 5. Hand it back

Open a PR against `person3-bounded-memory` (not `main`, for now) containing:
- `connectors/<type>.py` and its `REGISTRY` line
- `tests/test_connector_<type>.py`, passing, with the contract subclass and your own tests
- the live check script and how to run it
- the `pod-test` connections and releases you need (§7.3 of the plan), as a `test_account.json` change, once that file exists (step 2.2). Until then, list them in the PR description

**Never put tokens in the PR**, its description, a commit or an issue. Send them to Person 3 privately, who puts them in SSM.

CI runs `ruff`, `mypy`, `bandit` and `pytest`, so run them locally first.

## What's not here yet

| Piece | Step | Until then |
|---|---|---|
| The pipeline calling connectors for a product's release, so a bundle shows your file | 2.1 | `try_fetch` and the contract tests |
| `test_account.json`, `pod-test` and `CRA_TEST_ACCOUNT` (a full local bundle with `python app.py`) | 2.2 | As above |

## Moving a prototype onto the contract

- **GitHub** (`github_client.py`, `github_artifact_retrieval.py`, and `feature/dynamic-github-connector`): token-based, with no GitHub App. Each connection holds one repository and a **fine-grained, read-only token** for it.
  - **Settings:** `repository` (`owner/name`, reuse `GitHubConfig`'s check), plus `token` as the secret field.
  - **Ref:** `revision`, a 40-character commit SHA (pattern `[0-9a-f]{40}`), and `paths`, candidates like `artifacts/{product}/{release}/sbom.json`.
  - **Keep** `get_file`'s tree-then-blob walk and its rule that only a complete tree establishes absence. `ConfirmedArtifactMissing` on every candidate becomes `not_found`.
  - **Swap** urllib for `ctx.http` (`allowed_hosts` = `["api.github.com"]`) and `GITHUB_TOKEN` for `ctx.secret()`. Drop `allow_anonymous`.
  - **Errors:** 401/403 and a repository 404 become `auth_failed`; a rate limit (429, or 403 with `x-ratelimit-remaining: 0`), 5xx or timeout becomes `unavailable` (check the rate limit before treating 403 as auth); malformed tree or blob data becomes `source_error`.
  - **Drop** the `github` field on `POST /bundles`, plus its `spec.yaml` and README changes. The repository now comes from the company's connection.
  - **Version:** return the commit SHA as `version`.
- **Confluence** (`codex/confluence-attachment-reader`): `fetch_attachment` is most of `fetch()`. Changes:
  - `cloud_id` and `email` become settings, and the token becomes the secret field
  - urllib becomes `ctx.http`; it already checks the download redirect before following it, which matches rule 3
  - add the attachment version to the ref (rule 8)
  - error codes map as missing → `not_found`, ambiguous → `source_error`, 401/403 → `auth_failed`, network and 5xx → `unavailable`
  - add contract tests
