# Getting started: run, test and deploy

This guide assumes you have never used this project before. It walks through, in order:

1. [What this project is](#1-what-this-project-is)
2. [One-time setup on your computer](#2-one-time-setup-on-your-computer)
3. [Running the app locally](#3-running-the-app-locally)
4. [The data the app expects](#4-the-data-the-app-expects)
5. [Running our own tests (pytest)](#5-running-our-own-tests-pytest)
6. [Running the test runner locally](#6-running-the-test-runner-locally)
7. [Our live deployment](#7-our-live-deployment)
8. [Deploying and running the server](#8-deploying-and-running-the-server)
9. [Integrating once we are deployed](#9-integrating-once-we-are-deployed)
10. [Troubleshooting](#10-troubleshooting)

Every command goes in a **terminal**: Terminal on macOS, PowerShell on Windows. Lines starting
with `#` are comments, so you don't need to type them.

---

## 1. What this project is

The **Cybersecurity Interceptor** helps a company gather the technical documentation that the EU
Cyber Resilience Act (Article 31, Annex VII) requires for each product release. You give it a
product and a release version. It collects that release's documents (SBOM, risk assessment, test
logs, user instructions). Then it tells you which ones are present, missing or invalid.

The repository contains **two programs**:

| Program | File | What it does | Course name |
|---|---|---|---|
| **The app** | `app.py` | The documentation aggregator itself: a small web service (an "API") | the **system** |
| **The test runner** | `test_runner/` | A program that sends requests to *any* team's app and checks the answers | the **tester** |

Both are web services. You talk to them by sending HTTP requests with JSON bodies, using `curl`
in the terminal, Postman, or Python. Neither has a web page or buttons yet.

Both are live at **https://team22.13-58-35-224.sslip.io** (the app at `/system`, the test runner
at `/tester`). Section 7 explains how that server is set up.

---

## 2. One-time setup on your computer

### 2.1 Install the tools

| Tool | Check if you have it | Get it |
|---|---|---|
| Python 3.10 or newer | `python3 --version` (Windows: `python --version`) | https://www.python.org/downloads/ (on Windows, tick **"Add python.exe to PATH"**) |
| Git | `git --version` | https://git-scm.com/downloads |
| curl | `curl --version` | Built into macOS, Linux and Windows 10+ (on Windows type `curl.exe`, not `curl`) |

### 2.2 Download the code

```bash
git clone git@github.com:wood297/ECE-30861-Team-22.git
cd ECE-30861-Team-22
```

If `git@...` gives a "permission denied" error, use the HTTPS address instead:
`git clone https://github.com/wood297/ECE-30861-Team-22.git`

### 2.3 Create a virtual environment and install the libraries

A **virtual environment** (venv) is a private folder of Python libraries just for this project, so
it can't conflict with anything else on your computer.

macOS / Linux:

```bash
python3 -m venv .venv               # create it (only once)
source .venv/bin/activate           # turn it on (every new terminal window)
pip install -r requirements.txt     # install Flask, pytest, gunicorn (only once, or when the file changes)
```

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

When the venv is on, your prompt starts with `(.venv)`. **If you open a new terminal, run the
`activate` line again.** Most "ModuleNotFoundError: No module named 'flask'" errors happen because
the venv isn't on.

---

## 3. Running the app locally

### 3.1 Start it

```bash
python app.py
```

You should see `Running on http://127.0.0.1:5000`. Leave this terminal open: the app runs until
you press **Ctrl+C**. Open a **second terminal** (and activate the venv there too) to send requests.

> **Mac users:** macOS uses port 5000 for "AirPlay Receiver". If you see "Address already in use",
> or every request returns `403`, start the app on another port instead:
> `flask --app app run --port 5050`, and use `5050` instead of `5000` in every command below.

### 3.2 Check it is alive

```bash
curl http://localhost:5000/health
```

Expected answer: `{"status":"ok"}`

### 3.3 Ask it to collect a release's documents

This is the main thing the app does. It takes **two steps**, as agreed in the pod specification
(Phase 2, Section 5).

**Step 1: request a bundle** (`POST /bundles`):

```bash
curl -X POST http://localhost:5000/bundles \
     -H "Content-Type: application/json" \
     -d '{"product_id": "prd-88", "release_version": "v1.3"}'
```

> **Windows PowerShell:** quoting JSON on the command line behaves differently between PowerShell
> versions, so put the JSON in a file instead. Save `{"product_id": "prd-88", "release_version": "v1.3"}`
> as `body.json`, then run (on one line, with `curl.exe` rather than `curl`):
> `curl.exe -X POST http://localhost:5000/bundles -H "Content-Type: application/json" -d "@body.json"`
> Use the same trick for every other JSON example in this guide.

Answer (`202 Accepted`):

```json
{
  "bundle_id": "650d3b5e-d4d8-425d-a8ea-7bed612625ed",
  "product_id": "prd-88",
  "release_version": "v1.3",
  "status": "PENDING",
  "manifest": [],
  "artifacts": []
}
```

Copy the `bundle_id`. It is a random ID, so yours will be different.

**Step 2: look at the result** (`GET /bundles/<bundle_id>`):

```bash
curl http://localhost:5000/bundles/650d3b5e-d4d8-425d-a8ea-7bed612625ed
```

Answer (`200 OK`; shortened to one artifact):

```json
{
  "bundle_id": "650d3b5e-d4d8-425d-a8ea-7bed612625ed",
  "product_id": "prd-88",
  "release_version": "v1.3",
  "status": "PARTIAL_SUCCESS",
  "manifest": [
    {"category": "SBOM",              "status": "PRESENT", "external_source": "fixtures"},
    {"category": "Risk Assessment",   "status": "PRESENT", "external_source": "fixtures"},
    {"category": "Test Logs",         "status": "MISSING", "external_source": "fixtures"},
    {"category": "User Instructions", "status": "PRESENT", "external_source": "fixtures"}
  ],
  "artifacts": [
    {"category": "SBOM", "product_id": "prd-88", "release_version": "v1.3", "content": "# SBOM\n"}
  ],
  "download_url": "http://localhost:5000/bundles/650d3b5e-d4d8-425d-a8ea-7bed612625ed/download"
}
```

**Step 3 (optional): download the bundle** as a zip. It holds every `PRESENT` document under
its original file name, byte for byte, plus a `manifest.json`:

```bash
curl -o bundle.zip http://localhost:5000/bundles/650d3b5e-d4d8-425d-a8ea-7bed612625ed/download
```

### 3.4 What the request fields mean

`POST /bundles` needs a JSON body with exactly these two fields:

| Field | Type | Required | Example | Meaning |
|---|---|---|---|---|
| `product_id` | text | yes | `"prd-88"` | Which product. Must match a folder name in `fixtures/` (see Section 4). |
| `release_version` | text | yes | `"v1.2"` | Which release of that product. Must match a sub-folder name. |

Always send the header `Content-Type: application/json`.

### 3.5 What the response fields mean

| Field | Meaning |
|---|---|
| `bundle_id` | ID of this request. Use it in `GET /bundles/<bundle_id>`. |
| `status` | Overall state of the bundle (table below). |
| `manifest` | One entry per document category, with that category's status (table below). |
| `artifacts` | The documents that were found, with their full, unchanged text in `content`. |
| `download_url` | Link to the zip of the bundle. Present when `status` is `COMPLETED` or `PARTIAL_SUCCESS`. |
| `error` | Present only when `status` is `FAILED`; explains why. |

Each `manifest` entry also has `external_source` (always `"fixtures"` in Phase 3) and, when the
category is `INVALID` or `INACCESSIBLE`, a `source_failure_detail` saying why.

**Bundle status** (`status`):

| Value | Meaning |
|---|---|
| `PENDING` | Request received, not processed yet. You only see this in the first response. |
| `PROCESSING` | Documents are being collected and classified. Processing is quick, so you normally won't see this. |
| `COMPLETED` | Every category is `PRESENT`. |
| `PARTIAL_SUCCESS` | Some categories are not `PRESENT`. The bundle still holds the ones that are. |
| `FAILED` | The release doesn't exist, or the identifiers are unsafe (for example, they contain `/` or `..`). |

**Category status** (each `manifest` entry):

| Value | Meaning | How the app decides today |
|---|---|---|
| `PRESENT` | Found and usable | The file exists and has text in it |
| `MISSING` | Not found | The file doesn't exist |
| `INVALID` | Found but unusable | The file exists but is empty (or only spaces), or is not UTF-8 text |
| `INACCESSIBLE` | The source couldn't be reached | The file exists but couldn't be read (e.g. no permission). Live sources come in Phase 4. |

### 3.6 Errors you can get

| You send | You get |
|---|---|
| No `product_id`, or not a non-empty string | `400 {"error": "product_id is required and must be a nonempty string"}` |
| No `release_version`, or not a non-empty string | `400 {"error": "release_version is required and must be a nonempty string"}` |
| A release that doesn't exist | `202`, then `GET` shows `"status": "FAILED"` and an `error` message |
| A `bundle_id` that doesn't exist | `404 {"error": "Bundle not found"}` |

### 3.7 The testing endpoints on the app

The app also has `/test/setup` and `/test/report`, which the course requires so that any team's
test runner can prepare our app and read its coverage. You normally don't call these by hand. The
test runner does it for you (Section 6). They are described in [TESTING.md](TESTING.md#testing-endpoints-on-our-system).

---

## 4. The data the app expects

In Phase 3 the app doesn't connect to GitHub or Jenkins yet. It reads documents from **one
controlled source**: the `fixtures/` folder in this repository. (Live sources come in Phase 4.)

### 4.1 Folder layout

```
fixtures/
└── <product_id>/                  e.g. prd-88
    └── <release_version>/         e.g. v1.2
        ├── sbom.md                -> category "SBOM"
        ├── risk_assessment.md     -> category "Risk Assessment"
        ├── test_logs.md           -> category "Test Logs"
        └── user_instructions.md   -> category "User Instructions"
```

The rules:

- The **folder names are the identifiers.** `{"product_id": "prd-88", "release_version": "v1.2"}`
  reads `fixtures/prd-88/v1.2/`. Spelling and capital letters must match exactly.
- The **file names are fixed.** Each category has exactly one file name, listed above (and in
  `EXPECTED_FILES` in `artifact_pipeline.py`). Other files in the folder are ignored.
- Files are **Markdown** (`.md`), saved as **UTF-8** text. A file in another encoding currently
  crashes the request (known gap).
- **Missing file → `MISSING`. Empty file → `INVALID`. File with text → `PRESENT`.**
- The text is returned **exactly as written**. The app never edits it (requirement REQ-08).

### 4.2 The releases that already exist

| Request | Folder | What you'll see | Used by the tests for |
|---|---|---|---|
| `prd-88` / `v1.2` | all 4 files, with text | all 4 `PRESENT` | the "complete" case |
| `prd-88` / `v1.3` | no `test_logs.md` | Test Logs `MISSING` | the "missing" case |
| `prd-88` / `v1.4` | `sbom.md` is empty | SBOM `INVALID` | the "invalid" case |
| `prd-88` / anything else | no folder | bundle `FAILED` | the "unknown release" case |

**Do not edit these four.** Both pod teams' tests depend on them exactly as they are.

### 4.3 Adding your own release to try things out

```bash
mkdir -p fixtures/my-product/v2.0
echo "# SBOM for my-product v2.0" > fixtures/my-product/v2.0/sbom.md
echo "# Risk assessment"          > fixtures/my-product/v2.0/risk_assessment.md
```

Restarting the app isn't needed: files are read on every request. Asking for
`{"product_id": "my-product", "release_version": "v2.0"}` will now show SBOM and Risk Assessment
`PRESENT`, and Test Logs and User Instructions `MISSING`.

---

## 5. Running our own tests (pytest)

With the venv on, in the repository folder:

```bash
pytest
```

A healthy result today looks like:

```
98 passed, 1 skipped, 5 xfailed
```

- **passed**: working.
- **xfailed** ("expected failure"): a known gap that hasn't been built yet. These are listed in
  [TESTING.md](TESTING.md#defects-found-by-the-tests). They don't break the build.
- **XPASS**: a known gap that now works. Remove its "known gap" marker (TESTING.md explains how).
- **failed**: something broke. Read the message above the summary.

Useful variations: `pytest -v` (one line per test), `pytest tests/test_api.py` (one file),
`pytest -k AC-03` (tests whose name contains `AC-03`).

---

## 6. Running the test runner locally

### 6.1 What the test runner does

The test runner is our **automated acceptance suite**. You point it at any team's app (its "base
URL"). It then:

1. Sets up a **session**: it makes a unique session ID and sends it to the app's `/test/setup`.
2. Runs **15 tests** in order through the app's normal API: 7 interface checks (`API-01` to
   `API-07`) and the 8 acceptance criteria from our Phase 2 spec (`AC-01` to `AC-08`).
3. Asks the app's `/test/report` for code coverage.
4. Returns **one report** with a result for every test, plus coverage.

This is the course's *Cross-team Testing Interface*. The details are in [TESTING.md](TESTING.md).

### 6.2 The data the test runner needs

The test runner needs two things, both already in the repository:

| Data | Where | What it is |
|---|---|---|
| **Expected answers** | `test_runner/oracle.json` | For each test release: which category should be `PRESENT`, `MISSING` or `INVALID`. Also the list of components and acceptance criteria used for coverage. Written by hand, never generated from our own app. |
| **The fixture files** | `fixtures/prd-88/` | The tester hashes these files to check that the app returned their content unchanged (AC-06), and that no file from another release leaked in (AC-01). |

**The app being tested must serve the same fixtures** (`prd-88` `v1.2`, `v1.3`, `v1.4`) from its
own source. That is how both pod teams' testers can grade both teams' apps. If you change a
fixture or `oracle.json`, tell Team 24, or cross-team results will stop making sense.

### 6.3 Easiest way: the command line

You need **two terminals**, both with the venv on.

Terminal 1 starts the app:

```bash
python app.py
```

Terminal 2 runs the tester against it:

```bash
python -m test_runner --url http://localhost:5000
```

The output ends like this (today, against our own unfinished app):

```
[PASS] AC-06  Source fidelity: collected content is byte-for-byte identical to the source
...
Coverage:
  code      n/a    Source lines; per-session code coverage instrumentation is not implemented yet, ...
  component 6/7    Components listed in the pod specification ...
  system    7/8    Acceptance criteria AC-01..AC-08 ...

11 passed, 2 failed, 1 skipped, 1 errors in 20.7s
```

The 2 failures and 1 error are the known gaps in our app, not problems with the tester. The run
takes about 20 s, because the tester waits 5 s for each of 4 bundles to finish, and today they
never do.

Options:

| Option | Effect |
|---|---|
| `-v` | Show every individual check, not just failed ones |
| `--json` | Print the full JSON report (the format graders see) |
| `--only AC-02 AC-03` | Run only those tests |
| `--no-setup` | Skip `/test/setup` (for apps that don't have it yet) |
| `--poll-timeout 2` | Wait at most 2 s for each bundle to finish (default 5) |

### 6.4 The way graders use it: as a web service

Graders and other teams don't run our code on their laptops. They call our **deployed tester
service**. You can run that service locally to practise.

Terminal 1 starts the app on port 5000:

```bash
python app.py
```

Terminal 2 starts the tester service on port 5001:

```bash
python -m test_runner.service --port 5001
```

Terminal 3 drives the tester. This is exactly what a grader does.

```bash
# 1) Setup: tell the tester which app to test. It returns a session ID.
curl -X POST http://localhost:5001/test/setup \
     -H "Content-Type: application/json" \
     -d '{"target_url": "http://localhost:5000"}'
# -> 201 {"session_id": "0aec3e90-fa82-4324-9a76-f9736b812447"}

# 2) Run: start the tests for that session. This takes ~20 s and returns the full report.
curl -X POST http://localhost:5001/test/run \
     -H "Content-Type: application/json" \
     -d '{"session_id": "0aec3e90-fa82-4324-9a76-f9736b812447"}'

# 3) Report: fetch the same report again later, without re-running.
curl "http://localhost:5001/test/report?session_id=0aec3e90-fa82-4324-9a76-f9736b812447"
```

Rules to know:

- `target_url` is the app's **base URL**: no `/test` or `/bundles` on the end, no `?...`, and no
  password in it.
- **One run per session.** Running the same session again returns `409`. To test again, do
  step 1 again to get a new session.
- Asking for the report before the run finishes returns `409`. Wait and try again.
- If something goes wrong, the answer looks like `{"message": "...", "source": "..."}`. `source`
  tells you whose fault it was: `tester` (ours), `system` (the app being tested), `communication`
  (network or timeout), or `unknown`.

### 6.5 Reading the report

Each entry in `results` has:

| Field | Meaning |
|---|---|
| `test_id` | e.g. `AC-03` |
| `outcome` | `pass`, `fail` (wrong answer), `error` (couldn't evaluate; see `source`), `skip` (not run) |
| `message` | One sentence on what happened. For a `fail`, it names the check that failed. |
| `checks` | The individual checks inside the test, each `true`, `false` or `null` (not observable) |

`coverage` has three parts. Each has `covered`, `total` and a `basis` explaining what was counted.
`null` means "couldn't be measured", and `basis` says why.

### 6.6 Running both exactly like production (optional; macOS/Linux)

On the server, both services run together under **gunicorn**, a production web server, with the
app at `/system` and the tester at `/tester`. You can try the same thing locally:

```bash
gunicorn --workers 1 --threads 8 --timeout 180 --bind 127.0.0.1:8000 wsgi:application
```

Then use `http://localhost:8000/system` as the app's base URL and `http://localhost:8000/tester`
as the tester's:

```bash
curl -X POST http://localhost:8000/tester/test/setup -H "Content-Type: application/json" \
     -d '{"target_url": "http://localhost:8000/system"}'
```

gunicorn doesn't run on Windows. Windows users can skip this step or use WSL.

---

## 7. Our live deployment

Both services run on one AWS EC2 server at:

**https://team22.13-58-35-224.sslip.io**

| URL | What answers | What you get back |
|---|---|---|
| `/` | `wsgi.py`'s index, a health check for the whole server | `{"status": "ok", "system": "/system", "tester": "/tester"}` |
| `/system` | the app (`app.py`). This is our **System base URL**. | The list of the app's endpoints |
| `/system/health` | the app | `{"status":"ok"}` |
| `/tester` | the test runner (`test_runner/service.py`). This is our **Tester base URL**. | The list of the tester's endpoints |
| `/tester/health` | the test runner | `{"status":"ok"}` |
| anything else | `wsgi.py` | `404 {"message": "Not found", "source": "system"}` |

Two small things you'll notice with `curl`:

- `/system` and `/tester` with no trailing slash answer `308` and redirect to `/system/` and
  `/tester/`. Use `curl -L`, or add the slash. Real endpoints such as `/system/bundles` don't redirect.
- `http://` redirects to `https://`.

### 7.1 How a request reaches our code

```
other team's tester, or curl on your laptop
   │   https://team22.13-58-35-224.sslip.io/system/bundles
   ▼
sslip.io DNS        team22.13-58-35-224.sslip.io  →  13.58.35.224 (our Elastic IP)
   ▼
AWS security group  ports 80 and 443 open to everyone; port 22 (SSH) open to one IP only
   ▼
Caddy  :80, :443    HTTPS with a Let's Encrypt certificate; redirects http → https
   │   forwards to 127.0.0.1:8000 and adds X-Forwarded-* headers
   ▼
gunicorn            systemd service "cra": 1 worker, 8 threads, 180 s timeout
   ▼
wsgi.py             /system/... → app.py
                    /tester/... → test_runner/service.py
                    /           → the small index above
```

- **sslip.io** is a free DNS service: any name that ends in `<IP-with-dashes>.sslip.io` points to
  that IP, so we didn't have to buy a domain. `team22.` is only a label. If sslip.io itself is down,
  our URL stops working even though the server is fine.
- **Caddy** got the certificate from Let's Encrypt and renews it by itself before it expires
  (certificates last 90 days). Renewal needs port 80 open, so leave that rule in the security group.
  Caddy only answers for our host name, so `https://13.58.35.224` won't work.
- **gunicorn** listens on `127.0.0.1` only. Port 8000 can't be reached from outside, so every
  request goes through Caddy.
- **`wsgi.py`** trusts the `X-Forwarded-*` headers from Caddy (`ProxyFix`), so URLs the app builds,
  such as a bundle's `download_url`, start with `https://team22.13-58-35-224.sslip.io/system/`.
- **One worker** because bundles, archives, sessions and reports all live in that one process's
  memory. A second worker wouldn't see them.
- **`/system/test/selftest` is off** in production (`404`), because `CRA_ENABLE_SELFTEST` isn't
  set in the service.

### 7.2 What we have on AWS

Created by `deploy/aws_setup.sh`, all named or tagged `team22-cra`:

| Resource | Details |
|---|---|
| Region | **`us-east-2` (Ohio)**. Pick this region in the console, or you won't see any of the resources below. |
| EC2 instance | `t3.micro` (2 vCPU, 1 GiB RAM), Ubuntu 24.04, 8 GB disk. Standard CPU credits, so a busy CPU slows down instead of costing extra. **Termination protection is on.** |
| Elastic IP | `13.58.35.224`. It stays the same when the instance is stopped and started, so the URL does too. |
| Security group | SSH (22) from the IP of the laptop that ran the setup script only; HTTP (80) and HTTPS (443) from anywhere |
| CloudWatch alarm | `team22-cra-auto-recover`: if AWS's hardware under the instance fails, AWS moves it to new hardware. Same IP, same disk. |
| Budget | `team22-cra-monthly`: $15/month, emails at 80% of actual spend |
| SSH key pair | `team22-ec2`. The private key is `~/.ssh/team22-ec2` on Person 3's laptop only. |

Cost: about $11/month (the instance plus the public IPv4 address), paid from the AWS Free plan
credits.

### 7.3 What is on the server

```
/home/ubuntu/ECE-30861-Team-22/   the deployed code: a copy of one commit, NOT a git clone
    VERSION                       which branch@commit is deployed, e.g. person3-bundle-generation@35068a5
    .venv/                        Python virtual environment with requirements.txt installed
/etc/systemd/system/cra.service   runs gunicorn at boot and restarts it if it crashes
/etc/caddy/Caddyfile              team22.13-58-35-224.sslip.io { reverse_proxy 127.0.0.1:8000 }
```

`deploy/server_setup.sh` rewrites both config files on every deploy, so change the script, not the
files on the server. There is no `.git` folder on the server, so `git pull` doesn't work there. To
deploy, use 8.1.

**Nothing is saved to disk.** Bundles, their zip archives, tester sessions and reports are all held
in memory. A deploy, a reboot or a crash wipes them.

### 7.4 Why EC2

Phase 3 requires both services to be **deployed at a reachable URL** and registered in the class
spreadsheet. For our code, a host must:

1. **Run a single, always-on process.** Sessions and reports are stored in memory. If the host runs
   several copies, or puts the app to sleep, a session made by `setup` can vanish before `run`.
2. **Allow outgoing internet requests.** Our tester has to call other teams' apps.
3. **Give us a stable URL**, ideally HTTPS.
4. **Allow a request to take about 2 minutes.** A tester run waits for the whole suite.

One EC2 `t3.micro` with gunicorn behind Caddy is the simplest setup that meets all four. These are
the options we compared (checked in October 2026):

| Option | Cost for us | Fits our code? | Notes |
|---|---|---|---|
| **AWS EC2** (one small Linux server) | AWS accounts opened since 15 July 2025 get a Free plan with up to **$200 in credits**, for 6 months or until the credits run out. A `t3.micro` plus its public IP is roughly $11/month, so the credits cover the semester. | **Yes, best fit.** Always on, one process, full control. | **What we use.** |
| **AWS Lightsail** (simplified server) | About 3 months free on one small plan, then from about $5/month | Yes | Same idea as EC2 with a simpler console. |
| **AWS Elastic Beanstalk** | Free itself; you pay for the EC2 server it creates | Yes, if set to one instance | Adds load-balancer and environment settings to understand. |
| **AWS ECS Express Mode** (containers) | You pay for Fargate compute **and an Application Load Balancer** (roughly $16+/month for the load balancer alone) | Yes, if limited to one task | Would need a Dockerfile. Burns credits faster. |
| **AWS App Runner** | — | — | **Not available:** closed to new customers on 30 April 2026. |
| **AWS Lambda** (serverless) | Very generous free tier | **No.** Each request may hit a different copy, so in-memory sessions get lost. | Would need a database for sessions first. |
| Render (free web service) | Free | Risky | Sleeps after 15 min idle. Sleeping wipes our in-memory sessions. |
| Google Cloud Run | Always-free tier | Risky | Scales to zero and can start several copies. |

---

## 8. Deploying and running the server

All of this needs the SSH key `~/.ssh/team22-ec2`, and your IP has to be allowed in the security
group (8.3). Right now that means Person 3's laptop.

### 8.1 Deploy a new version

From the repository on your laptop:

```bash
git checkout <branch to deploy>     # deploy.sh deploys whatever is checked out, not main
git status                          # must be clean: no changes, no untracked files
deploy/deploy.sh 13.58.35.224
```

What `deploy.sh` does:

1. Packs the current commit with `git archive HEAD` (only committed files) and writes
   `branch@commit` into `VERSION`.
2. Copies it to `/home/ubuntu/ECE-30861-Team-22` with `rsync --delete`. Files that aren't in the
   commit are removed, except `.venv`.
3. On the server, runs `deploy/server_setup.sh`. It installs Caddy the first time, and rewrites
   `cra.service` and the `Caddyfile` every time.
4. Installs `requirements.txt` into `.venv` and runs `pytest`.
5. Restarts `cra`, then calls `http://127.0.0.1:8000/` to check it answers.

It ends with `Deployed <branch>@<commit> to https://team22.13-58-35-224.sslip.io`.

Things to know:

- **A restart wipes every bundle, session and report.** Don't deploy while another team is testing
  us.
- **If `pytest` fails on the server, the script stops before the restart.** The old version keeps
  serving, but the new files (and `VERSION`) are already on disk. Any later restart, including the
  automatic one after a crash or a reboot, would start the new, failing code. Fix the tests and
  deploy again soon.

Then check the deployment from your laptop (9.1). To see which commit is live:

```bash
ssh -i ~/.ssh/team22-ec2 ubuntu@13.58.35.224 cat ECE-30861-Team-22/VERSION
```

### 8.2 Logs and service commands

Log in with `ssh -i ~/.ssh/team22-ec2 ubuntu@13.58.35.224`, then:

```bash
sudo systemctl status cra            # is the app running?
sudo journalctl -u cra -n 100        # last 100 lines of app logs (errors, tracebacks)
sudo journalctl -u cra -f            # follow logs live (Ctrl+C to stop)
sudo systemctl restart cra           # restart the app (wipes in-memory data)
sudo journalctl -u caddy -n 50       # HTTPS/certificate problems
curl http://127.0.0.1:8000/          # talk to gunicorn directly, skipping Caddy
```

### 8.3 SSH access for you or a teammate

Port 22 is only open to the IP that `aws_setup.sh` ran from. If you're on a different network, or
your home IP changed, `ssh` just hangs and times out. Re-running `aws_setup.sh` won't fix it,
because it skips the security group once it exists. Instead, add your current IP:

```bash
MY_IP=$(curl -fsS https://checkip.amazonaws.com)
aws --region us-east-2 ec2 authorize-security-group-ingress \
    --group-name team22-cra --protocol tcp --port 22 --cidr "$MY_IP/32"
```

To give a teammate access:

1. They create a key with `ssh-keygen -t ed25519` and send you the **public** half (`.pub`).
2. You append it to `~/.ssh/authorized_keys` on the server.
3. You add their IP with the command above, using their IP instead of `$MY_IP`.

Never share the private key.

### 8.4 Rebuilding from scratch

If the server is lost, run both scripts again:

```bash
BUDGET_EMAIL=you@example.com deploy/aws_setup.sh   # prints the new IP and URL
deploy/deploy.sh <new IP>
```

`aws_setup.sh` is safe to re-run: it skips anything that already exists. A new Elastic IP means a
new URL (`team22.<new-ip-with-dashes>.sslip.io`). If that happens, update the endpoint registry
(9.2), tell Team 24, and replace the address in this guide.

### 8.5 When the course is over

Termination protection is on, so turn it off first. Then delete everything so the charges stop:

```bash
R="--region us-east-2"
ID=$(aws $R ec2 describe-instances --filters Name=tag:Name,Values=team22-cra \
     --query 'Reservations[0].Instances[0].InstanceId' --output text)
aws $R ec2 modify-instance-attribute --instance-id "$ID" --no-disable-api-termination
aws $R ec2 terminate-instances --instance-ids "$ID"
aws $R ec2 wait instance-terminated --instance-ids "$ID"
aws $R ec2 release-address --allocation-id $(aws $R ec2 describe-addresses \
     --filters Name=tag:Name,Values=team22-cra --query 'Addresses[0].AllocationId' --output text)
aws $R cloudwatch delete-alarms --alarm-names team22-cra-auto-recover
aws $R ec2 delete-security-group --group-name team22-cra
aws $R ec2 delete-key-pair --key-name team22-ec2
```

An Elastic IP that isn't released is still charged, even after the instance is gone. Delete the
`team22-cra-monthly` budget last, in Billing and Cost Management → Budgets.

---

## 9. Integrating once we are deployed

### 9.1 Check the deployment yourself first

From your laptop:

```bash
H=https://team22.13-58-35-224.sslip.io

curl $H/                                                 # {"status": "ok", "system": "/system", "tester": "/tester"}
curl $H/system/health                                     # {"status":"ok"}
curl -X POST $H/tester/test/setup -H "Content-Type: application/json" \
     -d "{\"target_url\": \"$H/system\"}"                 # 201 {"session_id": "..."}
curl -X POST $H/tester/test/run -H "Content-Type: application/json" \
     -d '{"session_id": "<id from the line above>"}'      # 200 + full report
```

This is our tester grading our own system, which the graders will also do. You can run the CLI
from your laptop against the deployed system too:

```bash
python -m test_runner --url https://team22.13-58-35-224.sslip.io/system
```

### 9.2 Fill in our row of the endpoint registry spreadsheet

| Field | What we enter |
|---|---|
| **System base URL** | `https://team22.13-58-35-224.sslip.io/system` |
| **Tester base URL** | `https://team22.13-58-35-224.sslip.io/tester` |
| **Intra-pod specification URL** | Link to the shared Phase 2 specification (v1.0.0) agreed with Team 24 |
| **Access / run notes** | "No authentication. Max times: tester setup 70 s, tester run 150 s (typically ~20 s), system setup 5 s. Test data: shared fixtures prd-88 v1.2–v1.4. Sessions are in memory and cleared on redeploy." |

The spreadsheet rules:

- Base URLs have **no** `/test` on the end, no `?query`, no `#fragment`, and no passwords.
- Never put credentials in the spreadsheet.

### 9.3 The Phase 3 cross-team check with Team 24

The course asks us to test **in both directions** at least once, and to record the teams, date,
results and problems. Tests don't need to pass. What is graded is that the interface works and
the reports are well formed.

**Our tester → their system:**

```bash
curl -X POST $H/tester/test/setup -H "Content-Type: application/json" \
     -d '{"target_url": "<Team 24 system base URL>"}'
curl -X POST $H/tester/test/run -H "Content-Type: application/json" \
     -d '{"session_id": "<id>"}'
```

**Their tester → our system:** send Team 24 our system base URL and ask them to run their tester
with it as `target_url`. Keep our server running, and don't redeploy while they test.

Record each run (a table in our team documentation is enough):

| Date | Tester | System | Session ID | pass / fail / error / skip | Problems found | Fix owner |
|---|---|---|---|---|---|---|
| 2026-10-__ | Team 22 | Team 24 | ... | ... | ... | ... |
| 2026-10-__ | Team 24 | Team 22 | ... | ... | ... | ... |

### 9.4 Things to agree with Team 24 before testing

These make cross-team results meaningful. Details are in
[TESTING.md](TESTING.md#decisions-the-pod-still-has-to-confirm).

1. Both systems serve the **same fixtures** (`fixtures/prd-88/v1.2`, `v1.3`, `v1.4`, byte for byte)
   and use the same category names (`SBOM`, `Risk Assessment`, `Test Logs`, `User Instructions`).
2. Our tester adds a **`session_id`** field to every `POST /bundles` body. Their system should
   ignore it (or use it), not reject it.
3. The **component list** we use for component coverage (in `test_runner/oracle.json`) should be
   adopted, or replaced, in the shared pod specification.
4. Both systems answer `POST /test/setup` with `201 {"session_id": "<the same ID>"}`.

---

## 10. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | The venv isn't on. Run `source .venv/bin/activate` (Windows: `.venv\Scripts\Activate.ps1`). |
| `Address already in use` / every request is `403` on a Mac | AirPlay is using port 5000. Use `flask --app app run --port 5050`. |
| `curl: (7) Failed to connect` | The app isn't running, or you used the wrong port. Check the first terminal. |
| `POST /bundles` returns `415` or `400` | You forgot `-H "Content-Type: application/json"`, or the JSON is malformed. On Windows, send the JSON from a file (Section 3.3). |
| Bundle is `FAILED` with "No documents found" | `product_id`/`release_version` don't match a folder under `fixtures/` exactly. |
| `500 Internal Server Error` on `POST /bundles` | A non-text `product_id` (e.g. a number). This is a known gap; send text. |
| Tester `/test/run` returns `404` | Unknown session: call `/test/setup` first, or the server restarted and forgot it. |
| Tester `/test/run` returns `409` | That session already ran. Call `/test/setup` again for a new one. |
| Tester `/test/setup` returns `500` with `"source": "communication"` | The tester can't reach `target_url`. Check the URL (no trailing `/test`), and that the target is up. On EC2, check the security group allows ports 80/443. |
| Deployed site doesn't load | Check `curl https://team22.13-58-35-224.sslip.io/`. If that fails, run `sudo systemctl status cra` and `sudo journalctl -u cra -n 100` on the server (8.2). Also check, in `us-east-2`, that the instance is running and the Elastic IP is attached. |
| `ssh` to the server hangs, then times out | Your IP isn't allowed in the security group. Add it (8.3). |
| `curl https://.../system` returns `308` | The address has no trailing slash. Use `/system/` or `curl -L`. Real endpoints such as `/system/health` are unaffected. |
| Bundle or session is "not found" right after a deploy | A deploy restarts the app, and that wipes everything in memory. Create it again. |
