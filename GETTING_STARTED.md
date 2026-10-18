# Getting started: run, test and deploy

This guide assumes you have never used this project before. It walks through, in order:

1. [What this project is](#1-what-this-project-is)
2. [One-time setup on your computer](#2-one-time-setup-on-your-computer)
3. [Running the app locally](#3-running-the-app-locally)
4. [The data the app expects](#4-the-data-the-app-expects)
5. [Running our own tests (pytest)](#5-running-our-own-tests-pytest)
6. [Running the test runner locally](#6-running-the-test-runner-locally)
7. [Choosing where to deploy](#7-choosing-where-to-deploy)
8. [Deploying on AWS EC2, step by step](#8-deploying-on-aws-ec2-step-by-step)
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

## 7. Choosing where to deploy

### 7.1 What any host must give us

Phase 3 requires both services to be **deployed at a reachable URL** and registered in the class
spreadsheet. For our code, a host must:

1. **Run a single, always-on process.** Sessions and reports are stored in memory. If the host runs
   several copies, or puts the app to sleep, a session made by `setup` can vanish before `run`.
2. **Allow outgoing internet requests.** Our tester has to call other teams' apps.
3. **Give us a stable URL**, ideally HTTPS.
4. **Allow a request to take about 2 minutes.** A tester run waits for the whole suite.

### 7.2 The options

Free-tier terms change often, so check the provider's pricing page before you sign up. The
details below were checked in October 2026.

| Option | Cost for us | Fits our code? | Notes |
|---|---|---|---|
| **AWS EC2** (one small Linux server) | AWS accounts opened since 15 July 2025 get a Free plan with up to **$200 in credits**, for 6 months or until the credits run out. A `t3.micro` plus its public IP is roughly $11/month, so the credits cover the semester. Older accounts may still have 750 free `t2/t3.micro` hours a month for 12 months. | **Yes, best fit.** Always on, one process, full control. | **Recommended.** Step-by-step in Section 8. |
| **AWS Lightsail** (simplified server) | About 3 months free on one small plan, then from about $5/month | Yes | Same steps as EC2 with a simpler console. A good alternative if EC2 feels like too much. |
| **AWS Elastic Beanstalk** | Free itself; you pay for the EC2 server it creates | Yes, if set to one instance | Automates the server setup, but adds load-balancer and environment settings to understand. Needs a `Procfile` containing the gunicorn command from 6.6. |
| **AWS ECS Express Mode** (containers, using our `Dockerfile`) | No fee for Express Mode, but you pay for Fargate compute **and an Application Load Balancer** (the load balancer alone is roughly $16+/month) | Yes, if limited to one task | Gives an HTTPS URL automatically. Burns credits faster. |
| **AWS App Runner** | — | — | **Not available:** closed to new customers on 30 April 2026. |
| **AWS Lambda** (serverless) | Very generous free tier | **No.** Each request may hit a different copy, so in-memory sessions get lost. | Would need a database such as DynamoDB for sessions first. |
| AWS Academy Learner Lab (if the course gives us one) | Free course credits | Partly | Labs end after a few hours, and servers stop with them. The public IP can change at each restart, which breaks the URL we registered. |
| Render (free web service) | Free | Risky | Sleeps after 15 min idle and takes about a minute to wake. Sleeping wipes our in-memory sessions. |
| Google Cloud Run | Always-free tier (about 2M requests/month) | Risky | Scales to zero and can start several copies. Set max instances to 1 and accept that idle time clears sessions. |
| Azure for Students / DigitalOcean (GitHub Student Developer Pack) | Student credits ($100 Azure; DigitalOcean via the pack) | Yes, on a VM | The same idea as EC2 on another cloud. |

**Recommendation:** deploy on **one AWS EC2 `t3.micro` running Ubuntu**, with gunicorn behind
**Caddy** (a web server that gets a free HTTPS certificate automatically). It is the simplest setup
that meets all four requirements. Both of our services share it:
`https://<host>/system` and `https://<host>/tester`.

---

## 8. Deploying on AWS EC2, step by step

Expect about 45 minutes the first time. Do this **once for the team**, and share the AWS login
only through a password manager, never in chat or in the repository.

> **Scripted version (what we actually used).** With the AWS CLI signed in (`aws login`), the
> steps below are automated by three scripts in `deploy/`:
>
> - `BUDGET_EMAIL=you@example.com deploy/aws_setup.sh` does 8.1–8.3 in `us-east-2`: SSH key
>   `~/.ssh/team22-ec2`, security group, t3.micro with termination protection and an
>   auto-recovery alarm, Elastic IP, and a $15/month budget.
> - `deploy/deploy.sh <elastic-ip>` does 8.5–8.8. It sends the current **commit** (the working
>   tree must be clean), runs `deploy/server_setup.sh` on the server (Caddy, systemd), runs the
>   tests there, and restarts the app. The deployed commit is in `VERSION` on the server.
>
> Our server: `13.58.35.224`, base URL **https://team22.13-58-35-224.sslip.io**
> (`/system` and `/tester`). Log in with `ssh -i ~/.ssh/team22-ec2 ubuntu@13.58.35.224`. The
> key is on Person 3's laptop only; to give a teammate access, add their public key to
> `~/.ssh/authorized_keys` on the server and their IP to the security group.

### 8.1 Protect yourself from surprise bills (5 min)

1. Sign in to the AWS console (https://console.aws.amazon.com).
2. Search for **Billing and Cost Management → Budgets → Create budget**. Choose the "Zero spend"
   or "Monthly cost" template and enter your email. AWS will email you before you spend money.
3. Pick **one region** and always use it, e.g. **US East (N. Virginia) `us-east-1`** (top-right
   corner of the console). Resources in other regions are invisible but still billed.

### 8.2 Launch the server (10 min)

1. Console → search **EC2** → **Launch instance**.
2. **Name:** `team22-cra`.
3. **Application and OS image:** **Ubuntu Server 24.04 LTS** (64-bit x86).
4. **Instance type:** `t3.micro` (or `t2.micro` if that is the one marked "Free tier eligible").
5. **Key pair:** *Create new key pair*, name `team22-key`, type RSA, format `.pem`. The browser
   downloads `team22-key.pem`. **Keep it safe:** it is the only way to log in to the server.
6. **Network settings → Edit**, and make sure there are three inbound rules:
   - SSH, port 22, source **My IP**
   - HTTP, port 80, source **Anywhere (0.0.0.0/0)**
   - HTTPS, port 443, source **Anywhere (0.0.0.0/0)**
7. **Storage:** the default 8 GB is enough.
8. **Launch instance.**

### 8.3 Give it a permanent address (3 min)

A server's public IP changes whenever it is stopped and started, and that would break the URL we
register. To fix the address:

1. EC2 → **Elastic IPs** → **Allocate Elastic IP address** → **Allocate**.
2. Select the new IP → **Actions → Associate Elastic IP address** → choose `team22-cra` →
   **Associate**.
3. Write the IP down, e.g. `3.91.20.45`. Below it is written as `<IP>`.

> Cost: AWS charges about $0.005/hour (about $3.60/month) for every public IPv4 address. An
> Elastic IP that is **not attached** to a running server is still charged, so release it when the
> course ends.

### 8.4 Log in to the server (2 min)

macOS / Linux:

```bash
chmod 400 ~/Downloads/team22-key.pem
ssh -i ~/Downloads/team22-key.pem ubuntu@<IP>
```

Windows PowerShell (OpenSSH is built in):

```powershell
ssh -i $HOME\Downloads\team22-key.pem ubuntu@<IP>
```

Type `yes` the first time. You are now typing commands **on the server**. The prompt shows
`ubuntu@ip-...`.

### 8.5 Install the code (5 min)

On the server:

```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/wood297/ECE-30861-Team-22.git
cd ECE-30861-Team-22
git checkout main            # or whichever branch we are deploying
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q          # sanity check: same result as on your laptop
```

If the repository is **private**, `git clone` will ask for a password. Use a GitHub **personal
access token** (GitHub → Settings → Developer settings → Fine-grained tokens, read-only access to
this one repository) as the password. Don't save the token in any file in the repository.

### 8.6 Keep the app running with systemd (5 min)

**systemd** is Linux's built-in service manager. It starts our app at boot and restarts it if it
crashes. Create the service file:

```bash
sudo tee /etc/systemd/system/cra.service > /dev/null <<'EOF'
[Unit]
Description=Team 22 CRA system + tester
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/ECE-30861-Team-22
ExecStart=/home/ubuntu/ECE-30861-Team-22/.venv/bin/gunicorn --workers 1 --threads 8 --timeout 180 --bind 127.0.0.1:8000 wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cra
curl http://127.0.0.1:8000/          # -> {"status": "ok", "system": "/system", "tester": "/tester"}
```

Keep `--workers 1`. More workers would split the in-memory sessions between processes, and a
session created by `setup` would then be "unknown" when `run` arrives.

### 8.7 Put HTTPS in front with Caddy (10 min)

Caddy receives web traffic on ports 80/443, gets a free HTTPS certificate from Let's Encrypt,
and passes requests to gunicorn. A certificate needs a domain name. If we don't own one, we can
use **sslip.io**, a free service where `<IP-with-dashes>.sslip.io` automatically points to that IP.
For `3.91.20.45` that is `3-91-20-45.sslip.io`.

Install Caddy (official instructions: https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Configure it. Replace the host name with yours:

```bash
sudo tee /etc/caddy/Caddyfile > /dev/null <<'EOF'
3-91-20-45.sslip.io {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl reload caddy
```

From **your laptop**, check it works:

```bash
curl https://3-91-20-45.sslip.io/
```

If the certificate step fails (`sudo journalctl -u caddy` shows the reason), plain HTTP still works
and is allowed. Set the first line of the Caddyfile to `:80 {` instead and use `http://<IP>`.

### 8.8 Updating after new code is pushed

On the server:

```bash
cd ~/ECE-30861-Team-22
git pull
.venv/bin/pip install -r requirements.txt     # only needed if requirements.txt changed
.venv/bin/pytest -q
sudo systemctl restart cra
```

A restart clears all sessions and stored reports. Don't redeploy while another team is in the
middle of testing us.

### 8.9 Watching logs and fixing problems

```bash
sudo systemctl status cra            # is it running?
sudo journalctl -u cra -n 100        # last 100 lines of app logs (errors, tracebacks)
sudo journalctl -u cra -f            # follow logs live (Ctrl+C to stop)
sudo journalctl -u caddy -n 50       # HTTPS/certificate problems
```

### 8.10 Optional: Docker instead of a venv

The repository has a `Dockerfile` that runs the same gunicorn command. To use it:

1. Install Docker: `sudo apt install -y docker.io`.
2. Build the image: `sudo docker build -t cra .`
3. Run it: `sudo docker run -d --restart always -p 127.0.0.1:8000:8000 cra`

Use that in place of steps 8.5–8.6. Caddy stays the same. (The Dockerfile hasn't been
built-tested yet.)

### 8.11 When the course is over

To stop all charges:

- EC2 → Instances → select → **Instance state → Terminate**.
- EC2 → Elastic IPs → **Release**.

---

## 9. Integrating once we are deployed

### 9.1 Check the deployment yourself first

From your laptop, using our real base URL (examples use `https://3-91-20-45.sslip.io`):

```bash
H=https://3-91-20-45.sslip.io

curl $H/system/health                                     # {"status":"ok"}
curl -X POST $H/tester/test/setup -H "Content-Type: application/json" \
     -d "{\"target_url\": \"$H/system\"}"                 # 201 {"session_id": "..."}
curl -X POST $H/tester/test/run -H "Content-Type: application/json" \
     -d '{"session_id": "<id from the line above>"}'      # 200 + full report
```

This is our tester grading our own system, which the graders will also do. You can run the CLI
from your laptop against the deployed system too:

```bash
python -m test_runner --url https://3-91-20-45.sslip.io/system
```

### 9.2 Fill in our row of the endpoint registry spreadsheet

| Field | What we enter |
|---|---|
| **System base URL** | `https://3-91-20-45.sslip.io/system` |
| **Tester base URL** | `https://3-91-20-45.sslip.io/tester` |
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
| Deployed site doesn't load | Run `sudo systemctl status cra` and `sudo journalctl -u cra -n 100` on the server. Also check the security group, and that the Elastic IP is attached. |
