# Jenkins test data: step by step

> **Partly out of date (2026-11-19).** Parts A–C (test data and Jenkins builds) are done and still correct. Parts D–E describe the v1 design (`JENKINS_*` environment variables, `/etc/cra/secrets.env`, `jenkins_live_check.py`), which the connector kit replaced. For tokens and checks, follow [PHASE5_JENKINS_PLAN_V2.md](PHASE5_JENKINS_PLAN_V2.md) §5–§6 and `python -m connectors.account_live_check`.

This is the checklist for getting our **Test Logs** test data into Jenkins and connecting our service to it. It assumes no Jenkins experience. Do the parts in order. Each step says how to check it worked before you move on.

- **Jenkins server:** `https://jenk-30861.jscarfo.com/` (run by Team 24)
- **Login:** user `team22`. The password is shared privately inside the team. Never put it in git, the shared spreadsheet, a screenshot or a message to another team.
- **Background:** [PHASE5_JENKINS_PLAN.md](PHASE5_JENKINS_PLAN.md). **Team 24's half:** [TEAM24_CLOUDFLARE_ALLOWLIST.md](TEAM24_CLOUDFLARE_ALLOWLIST.md).

---

## 0. What you are building

```
 hand-written file        Jenkins build              our catalog                   bundle
 ─────────────────        ─────────────              ───────────                   ──────
 cra-artifact-test   ──►  pod/cra-test-logs #2  ──►  source_catalog.json   ──►  Test Logs PRESENT
 (GitHub, pinned SHA)     downloads + archives       "job", "build", "path"     external_source: Jenkins
                          the file; kept forever
```

**Words you need:**
- A **job** is a saved recipe.
- A **build** is one numbered run of it (#1, #2…). Numbers are never reused.
- An **artifact** is a file a build saved. **Our service reads artifacts.**
- **Keep this build forever** stops Jenkins from ever deleting a build.

**The three shared releases** (both teams' systems must give the same answers):

| Release | Build (expected) | What the build archives | Our service reports |
|---|---|---|---|
| `prd-88/v1.5-jenkins` | #2 | the hand-written report below | Test Logs **PRESENT**, bundle COMPLETED |
| `prd-88/v1.5-jenkins-missing` | #3 | nothing | Test Logs **INACCESSIBLE**, bundle PARTIAL_SUCCESS |
| `prd-88/v1.5-jenkins-empty` | #4 | an empty `test_logs.md` | Test Logs **INVALID**, bundle PARTIAL_SUCCESS |

The other three categories (SBOM, Risk Assessment, User Instructions) for these releases are copies of the `prd-88 v1.2` files that both teams already have byte for byte. They're in `fixtures/prd-88/v1.5-jenkins*/`.

**The test data is already written.** It's in our repo, laid out exactly as it must appear in GitHub:

| File in our repo | sha256 |
|---|---|
| `tests/fixtures/jenkins/artifacts/prd-88/v1.5-jenkins/test_logs.md` | `f5cf3fd835fdbbe957222dcc18a4fa12d3d192936e20a7eeee7cea22b1658173` |
| `tests/fixtures/jenkins/artifacts/prd-88/v1.5-jenkins-empty/test_logs.md` (0 bytes) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

There is deliberately no file for `-missing`. The offline tests serve these same files in place of Jenkins, and `jenkins_live_check.py` compares Jenkins against them. **Don't edit them** unless you redo Parts A and C. If you change the report, the sha256 changes and every build must be redone.

---

## Part A. Put the test data in GitHub (10 minutes)

The Jenkins job downloads the file from the public repo `prithika-sundar/cra-artifact-test`, the same repo the GitHub connector uses. Someone with push access does this part (Prithika, or a collaborator she adds).

**A1. Get both repos side by side**
```zsh
cd ~/Desktop/30861/project
git clone git@github.com:prithika-sundar/cra-artifact-test.git   # skip if you already have it
cd cra-artifact-test && git pull
```

**A2. Copy the files, keeping every byte.** Use `cp`; don't open them in an editor and re-save.
```zsh
SRC=../ECE-30861-Team-22/tests/fixtures/jenkins/artifacts/prd-88
mkdir -p artifacts/prd-88/v1.5-jenkins artifacts/prd-88/v1.5-jenkins-empty
cp "$SRC/v1.5-jenkins/test_logs.md"       artifacts/prd-88/v1.5-jenkins/
cp "$SRC/v1.5-jenkins-empty/test_logs.md" artifacts/prd-88/v1.5-jenkins-empty/
```

**A3. Check the bytes.** You should see the two hashes from the table in §0.
```zsh
shasum -a 256 artifacts/prd-88/v1.5-jenkins/test_logs.md artifacts/prd-88/v1.5-jenkins-empty/test_logs.md
```

**A4. Commit and push**
```zsh
git add artifacts/prd-88/v1.5-jenkins artifacts/prd-88/v1.5-jenkins-empty
git commit -m "Add Phase 5 Jenkins test logs for prd-88 v1.5-jenkins and v1.5-jenkins-empty"
git push
```

**A5. Copy the full commit SHA** (40 characters). You'll paste it into every build.
```zsh
git rev-parse HEAD
```

**A6. Check that GitHub serves the same bytes.** Replace `<SHA>`. The hash must match §0 again.
```zsh
curl -sL "https://raw.githubusercontent.com/prithika-sundar/cra-artifact-test/<SHA>/artifacts/prd-88/v1.5-jenkins/test_logs.md" | shasum -a 256
```
If it differs, git changed the line endings. Check `git config core.autocrlf`; it should be unset or `false`. Then recommit.

---

## Part B. One-time Jenkins setup (15 minutes, browser)

**B1. Log in** at `https://jenk-30861.jscarfo.com/` as `team22`. The Dashboard is empty, and there's no *Manage Jenkins* link. Both are normal: we aren't admins.

**B2. Make your API token** (for your terminal checks in Part D)
1. Top right **team22** → **Security** (or **Configure**) → **API Token** → **Add new Token**.
2. Name it `laptop-<yourname>` → **Generate** → **copy it now**. It's shown only once. Click **Save** if there's a Save button.
3. In our repo, copy `.env.example` to `.env` (git-ignored) and fill in:
   ```
   JENKINS_URL=https://jenk-30861.jscarfo.com
   JENKINS_USER=team22
   JENKINS_API_TOKEN=<your token>
   ```
Everyone shares the `team22` account, so you'll see teammates' tokens listed. Only ever revoke your own.

**B3. Create the shared folder.** First confirm with Team 24 that we're creating `pod`.
1. **+ New Item** → name `pod` → **Folder** → **OK**.
2. Description: "Shared Team 22 / Team 24 CRA test data. Do not delete or rebuild pinned builds." → **Save**.

**B4. Create the job**
1. Inside `pod`: **+ New Item** → name `cra-test-logs` → **Pipeline** → **OK**.
2. Scroll to **Pipeline**. Leave *Definition* on **Pipeline script**. Paste the **whole** of [jenkins/cra-test-logs.Jenkinsfile](../jenkins/cra-test-logs.Jenkinsfile).
3. Keep **Use Groovy Sandbox** ticked → **Save**.

**B5. Run build #1 (it fails on purpose).** Click **Build Now**. Build #1 goes red with `PRODUCT_ID: parameter not set` (or `Invalid RELEASE_VERSION`). That's expected: the first run is how Jenkins learns the job's parameters. The button now reads **Build with Parameters**.

If build #1 instead shows `curl: not found`, ask Team 24 to install curl on the Jenkins machine (`sudo apt-get install -y curl`), then run Build Now once more. The numbering in Part C then starts one later, which is fine (see D1).

---

## Part C. Run the three builds (10 minutes)

For each row: **Build with Parameters**, fill in the values, **Build**, wait for it to finish.

| Build | `PRODUCT_ID` | `RELEASE_VERSION` | `SOURCE_REPO` | `SOURCE_COMMIT` | `ARCHIVE_FILE` |
|---|---|---|---|---|---|
| #2 | `prd-88` | `v1.5-jenkins` | (default) | SHA from A5 | ☑ ticked |
| #3 | `prd-88` | `v1.5-jenkins-missing` | (default) | SHA from A5 | ☐ **unticked** |
| #4 | `prd-88` | `v1.5-jenkins-empty` | (default) | SHA from A5 | ☑ ticked |

After **each** build, open it (click `#2` etc. under *Builds*) and check:

| Check | #2 | #3 | #4 |
|---|---|---|---|
| Status | green | green | green |
| Description (top of page) | `prd-88/v1.5-jenkins` | `prd-88/v1.5-jenkins-missing (nothing archived)` | `prd-88/v1.5-jenkins-empty` |
| **Build Artifacts** | `artifacts/prd-88/v1.5-jenkins/test_logs.md` | none | `artifacts/prd-88/v1.5-jenkins-empty/test_logs.md` |
| **Console Output**, `sha256sum` line | `f5cf3fd8…8173` | (no such line) | `e3b0c442…b855` |

Then click **Keep this build forever** on each of #2, #3 and #4. It's a button near the top right of the build page.

- If the button isn't there, our account lacks that permission. Ask Team 24 to grant it, or to click it for us.
- **If you make a mistake:** don't delete anything. Run another build with the right values, pin that one, and use its number in D1.

---

## Part D. Connect our code (15 minutes, terminal)

**D1. Build numbers.** [source_catalog.json](../source_catalog.json) already points at `pod/cra-test-logs` builds **2, 3, 4**. If Jenkins gave different numbers, edit the three `"build"` values to match.

**D2. Load your token into the terminal**
```zsh
cd ~/Desktop/30861/project/ECE-30861-Team-22
set -a; source .env; set +a
```

**D3. Check the token and that the job is visible**
```zsh
python -m jenkins_auth_check
```
- Good: `{"status": "connected", "user": "team22", "jobs": ["pod/cra-test-logs"]}`
- `"code": "edge_blocked"` means Cloudflare refused Python's request. Team 24 must apply [TEAM24_CLOUDFLARE_ALLOWLIST.md](TEAM24_CLOUDFLARE_ALLOWLIST.md). Laptops are not on that allowlist, so this may stay blocked on your laptop. Do D3 and D4 on EC2 in Part E instead.

**D4. Compare every pinned build with the committed files**
```zsh
python jenkins_live_check.py
```
You should get three lines with `"ok": true` and exit code 0 (`echo $?`). Any `"ok": false` means a build doesn't hold what the catalog says. Read its `retrieval_status`.

**D5. You can also check by hand with curl,** which Cloudflare lets through:
```zsh
J() { curl -g -sS -u "$JENKINS_USER:$JENKINS_API_TOKEN" "$JENKINS_URL$1"; }
J '/job/pod/job/cra-test-logs/2/api/json?tree=number,building,result,artifacts[relativePath]'
J '/job/pod/job/cra-test-logs/2/artifact/artifacts/prd-88/v1.5-jenkins/test_logs.md' | shasum -a 256
```
These are the exact two requests our service makes for each release.

**D6. Run the offline tests, then commit**
```zsh
pytest -q
```

---

## Part E. The server (EC2)

Our deployed service needs its own token. It also needs Team 24 to have let our EC2 address through Cloudflare first.

**E1.** Send Team 24 [TEAM24_CLOUDFLARE_ALLOWLIST.md](TEAM24_CLOUDFLARE_ALLOWLIST.md) and wait for their "done".

**E2.** In Jenkins, make a token named `ec2-cra-service` (as in B2). Copy it.

**E3. Create the secrets file on EC2** (`ssh ubuntu@13.58.35.224`). It lives outside the release folders, so deploys and rollbacks never touch it. It is root-only.
```bash
sudo install -d -m 755 /etc/cra
sudo install -m 600 -o root -g root /dev/null /etc/cra/secrets.env
sudo nano /etc/cra/secrets.env
```
Put these three lines in it (no quotes), then save:
```
JENKINS_URL=https://jenk-30861.jscarfo.com
JENKINS_USER=team22
JENKINS_API_TOKEN=<the ec2-cra-service token>
```

**E4. Load it into the service.** [deploy/server_setup.sh](../deploy/server_setup.sh) now adds `EnvironmentFile=-/etc/cra/secrets.env` to the service on every deploy. After this branch is deployed, restart once so the service picks the file up:
```bash
sudo systemctl restart cra
```

**E5. Run both checks on EC2.** This is the address that matters.
```bash
cd /home/ubuntu/cra/current
sudo bash -c 'set -a; . /etc/cra/secrets.env; set +a; .venv/bin/python -m jenkins_auth_check'
sudo bash -c 'set -a; . /etc/cra/secrets.env; set +a; .venv/bin/python jenkins_live_check.py'
```
Both must exit 0. `edge_blocked` here means the Cloudflare rule isn't working yet.

**E6. Check the deployed service end to end**
```bash
curl -s -X POST https://team22.13-58-35-224.sslip.io/system/bundles \
     -H 'Content-Type: application/json' \
     -d '{"product_id":"prd-88","release_version":"v1.5-jenkins"}'
# then: curl -s https://team22.13-58-35-224.sslip.io/system/bundles/<bundle_id>
# expect "status": "COMPLETED" and Test Logs with "external_source": "Jenkins"
```

**Rotating the token:** revoke `ec2-cra-service` in Jenkins, make a new one, put it in `/etc/cra/secrets.env`, then `sudo systemctl restart cra`.

---

## Part F. Tell Team 24 (pod doc)

Post this so their system can pin the same builds:
- job `pod/cra-test-logs`, builds **#2 / #3 / #4**, and the artifact path for each release (table in §0)
- GitHub source: `prithika-sundar/cra-artifact-test` at commit `<SHA from A5>`
- sha256 of each Test Logs file (§0), plus the v1.2 files reused for the other categories:
  - risk `0c17a18b18d7…`
  - SBOM `4bf92cff3143…`
  - instructions `2201be0bb4a5…`
- the expected statuses (§0 table), and that an empty file is **INVALID**. Agree this before adding the releases to the shared oracle.

---

## Rules for a shared server

- Create things only inside `pod/` (agreed with Team 24) and `team22/`. Never touch other teams' folders.
- Never delete or rebuild a pinned build. Re-running the job only adds new numbers, which is harmless.
- Keep builds tiny. Every team shares 2 build slots.
- If you change the Jenkinsfile, paste it into every job that uses it and commit it.
- The password stays in the team. Programs use tokens.

## When something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| `401` / `"code": "authentication"` | Wrong user or token, or the token was revoked | Check `.env`; make a new token |
| `"code": "edge_blocked"`, or a 403 page mentioning Cloudflare | Cloudflare refused the request, not Jenkins | Team 24's allowlist (Part E1); use curl or the browser meanwhile |
| `403` from Jenkins / `"code": "permission"` | Our account lacks a permission | Ask Team 24 |
| `"code": "build_access"` / `404` | Typo in folder, job or build number, or a missing `/job/` piece | Click through in the browser and copy the URL |
| Build fails at **Check parameters** | Empty field or a short SHA | Use the full 40-character SHA |
| Build fails at `curl … 404` | File isn't at that path in that commit | Open `https://github.com/prithika-sundar/cra-artifact-test/blob/<SHA>/artifacts/prd-88/<release>/test_logs.md` |
| Live check `"ok": false` with `RETRIEVED` | The build holds different bytes | Re-do A3/A6, then a new build with the right SHA; update D1 |
| Live check `INACCESSIBLE:not_found` for `v1.5-jenkins` | Catalog points at the wrong build (e.g. #3) | Fix the numbers in D1 |
| Build waits in the queue | Both build slots are busy (other teams) | Wait |
