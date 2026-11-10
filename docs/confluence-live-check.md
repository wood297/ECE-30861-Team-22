# Confluence live download check

Use Python 3.10 or newer. The check uses the Python standard library.
Sign in with your own Atlassian account and verify that you can open the
[Confluence test page](https://whcora.atlassian.net/wiki/spaces/T2CT/pages/622599/Phase+5+Risk+Assessment+Retrieval+Suite).
Create a scoped Confluence API token with the read:attachment:confluence scope.

From the repository root, run this command with your Atlassian email:

    python -m confluence_live_check 622599 tests/fixtures/confluence/risk_assessment.md --cloud-id 43c0f648-dc39-4501-a7ca-bc8bf841ad48 --email YOUR_ATLASSIAN_EMAIL

Paste your token at the hidden prompt. A successful run verifies the original
bytes and prints SHA-256 83540a9d4ffa67ac2ab287dd7a4be80f79bf5705d24d6ca2cbb4985992895958.
