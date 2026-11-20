# Request to Team 24: let Team 22's server reach Jenkins through Cloudflare

**For:** whoever manages the Cloudflare account for `jscarfo.com`
**From:** Team 22
**Time needed:** about 10 minutes

## The problem

Our deployed CRA service runs on AWS EC2 at **`13.58.35.224`**. It reads test-log artifacts from your Jenkins (`https://jenk-30861.jscarfo.com`) with Python's standard HTTP client.

Those requests never reach Jenkins: **Cloudflare answers them itself with a 403**. The response has `Server: cloudflare` and no `X-Jenkins` header. A browser or `curl` with the same login gets through.

So something in Cloudflare's security settings is filtering by client type. We're asking you to exempt **only our server's IP address, only on the Jenkins hostname**. Jenkins still requires a login for every request, so this doesn't open Jenkins to anyone.

## What our requests look like

You can use this to recognise our traffic in Cloudflare's logs.

| | |
|---|---|
| Source IP | `13.58.35.224` (our EC2 public IP; we'll tell you if it ever changes) |
| Host | `jenk-30861.jscarfo.com` |
| Method | `GET` only. We never create, build or change anything from the service. |
| Auth | HTTP Basic, user `team22`, with a Jenkins API token |
| User-Agent | `cra-documentation-service` |
| Paths | `/whoAmI/api/json`, `/job/pod/job/cra-test-logs/<build>/api/json?tree=…`, `/job/pod/job/cra-test-logs/<build>/artifact/…` |
| Volume | A few requests per bundle; found files are cached, so usually far fewer |

---

## Step 1. Find which Cloudflare feature blocked us (2 minutes)

1. Log in at `https://dash.cloudflare.com` and click the **jscarfo.com** domain.
2. Open **Security → Events**. In newer dashboards this is **Security → Analytics**, then the **Events** tab.
3. Click **Add filter** → **Source IP** → *equals* → `13.58.35.224`. If there are no events from it yet, filter on **User agent** *contains* `Python-urllib` instead; that's the same block, seen from a teammate's laptop.
4. Open one event and read the **Service** field. Typical values:

| Service shown | Go to |
|---|---|
| Browser Integrity Check | Step 2 (most likely) |
| Security Level, Managed rules, Custom rules, Rate limiting | Step 2 |
| **Bot fight mode** | Step 3 |
| Super Bot Fight Mode (Pro plan or higher) | Step 2 (it can be skipped there) |

If you'd rather skip this step, do Step 2. If we still get blocked afterwards, it's Bot Fight Mode, so do Step 3.

## Step 2. Add a "Skip" rule for our IP (5 minutes)

1. In the **jscarfo.com** domain, open **Security → WAF → Custom rules** → **Create rule**.
2. **Rule name:** `Allow Team 22 CRA service to Jenkins`
3. Under *If incoming requests match…*, click **Edit expression** and paste:
   ```
   (ip.src eq 13.58.35.224 and http.host eq "jenk-30861.jscarfo.com")
   ```
   If your own system's Jenkins connector is blocked the same way, include your server's IP too:
   ```
   (ip.src in {13.58.35.224 <your-server-ip>} and http.host eq "jenk-30861.jscarfo.com")
   ```
4. **Choose action:** **Skip**.
5. **WAF components to skip:** tick every box shown, which normally includes:
   - All remaining custom rules
   - All rate limiting rules
   - All managed rules
   - All Super Bot Fight Mode Rules (only shown on Pro plans and above)
6. **More components to skip** (expand it): tick **Browser Integrity Check** and **Security Level**. Also tick **User Agent Blocking** and **Zone Lockdown** if they're listed.
7. **Log matching requests:** on, so you can see the rule working.
8. **Place at:** **First**.
9. **Deploy**.

## Step 3. Only if the Service was "Bot fight mode" (free plan)

The free plan's Bot Fight Mode can't be skipped by a custom rule or for a single IP. Pick one:

- **(a) Simplest:** turn it off for the domain under **Security → Bots → Bot Fight Mode → Off**. Jenkins still requires a login for everything, so the server stays protected.
- **(b) Keep Bot Fight Mode on:** give us a second hostname such as `jenk-api-30861.jscarfo.com`.
  1. Set its DNS record to **DNS only** (grey cloud), so it bypasses Cloudflare.
  2. Point it at the same Jenkins with a valid certificate (for example Caddy with Let's Encrypt).
  3. Limit that port in the server's firewall to `13.58.35.224`.
  4. Tell us the hostname, and we'll change one setting on our side.

## Step 4. Tell us it's done

Message us. We then run this on our EC2 server:
```
python -m jenkins_auth_check
```
We expect `{"status": "connected", "user": "team22", ...}`. Your **Security → Events** log should show our requests with the action **Skip**. If we still see `edge_blocked`, we'll send you the time of the attempt so you can find the event.

---

## Other things we need from you (no Cloudflare involved)

1. **Folders.** We plan to create `pod/` (shared Team 22/24 test data) and `team22/` at the top level of Jenkins. Is that OK, and do you want a `team24/`?
2. **Keep forever.** Can the `team22` account click "Keep this build forever"? If not, please grant that permission, or click it for builds #2–#4 of `pod/cra-test-logs`.
3. **No cleanup.** Please make sure no global setting discards old builds. Nobody should delete or rebuild builds in `pod/` or `team22/`.
4. **Uptime.** Please give us a heads-up before restarting Jenkins, and keep it up during agreed cross-team test windows.
5. **curl.** The job uses `curl` and `sha256sum` on the built-in node. If build #1 says `curl: not found`, please run `sudo apt-get install -y curl`.
6. **Pod test rules.** Before we add the shared releases to the oracle, please confirm both systems agree on these statuses:
   - an empty `test_logs.md` is **INVALID**
   - an artifact missing from a pinned build is **INACCESSIBLE**
   - Jenkins being unreachable is **INACCESSIBLE**

Thank you!
