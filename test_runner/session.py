"""Tester-side half of the cross-team testing interface: preparing a target system.

The tester generates a session ID and passes it to the system's POST /test/setup,
then waits until the system answers with the same ID (Cross-team Testing Interface,
"Required calls"). Shared by the tester web service and the command-line runner.
"""
import http.client
import json
import urllib.error

ERROR_SOURCES = {"tester", "system", "communication", "unknown"}
# No response at all: connection refused, DNS failure, timeout, dropped connection
NETWORK_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException)
# A system may start resources on demand during setup (e.g. a cold start), so allow longer
SETUP_TIMEOUT = 60


class SetupError(Exception):
    """System setup did not succeed; carries the status, message and source to return."""

    def __init__(self, status, message, source):
        super().__init__(message)
        self.status = status
        self.message = message
        self.source = source

    def body(self):
        return {"message": self.message, "source": self.source}


def _short(value, limit=200):
    text = value if isinstance(value, str) else json.dumps(value) if value is not None else "no body"
    return text if len(text) <= limit else text[:limit] + "..."


def setup_target(client, session_id, setup=None, timeout=SETUP_TIMEOUT):
    """Call the system's POST /test/setup and return once it is ready, or raise SetupError."""
    try:
        resp = client.request("POST", "/test/setup", timeout=timeout,
                              json_body={"session_id": session_id, "setup": setup or {}})
    except NETWORK_ERRORS as exc:
        raise SetupError(500, f"System setup could not be reached: {exc}", "communication")

    body = resp.json()
    if resp.status in (200, 201):
        if isinstance(body, dict) and body.get("session_id") == session_id:
            return
        raise SetupError(500, f"System setup returned HTTP {resp.status} without echoing session_id "
                              f"{session_id!r}: {_short(body)}", "system")

    # Forward the system's own status, message and source when it provides them
    status = resp.status if resp.status >= 400 else 500
    body = body if isinstance(body, dict) else {}
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raw = resp.body.decode("utf-8", "replace").strip()
        message = f"System setup failed with HTTP {resp.status}: {_short(raw or None)}"
    source = body.get("source") if body.get("source") in ERROR_SOURCES else "system"
    raise SetupError(status, message, source)
