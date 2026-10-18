"""Production entry point: serves both services from one host.

    /system/...   the validated system (app.py)            -> register as the System base URL
    /tester/...   the tester service (test_runner/service.py) -> register as the Tester base URL
    /             a small index, useful as a health check

Run with:
    gunicorn --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:8000 wsgi:application

Keep --workers at 1: both services keep sessions in memory, so every request for a
session must reach the same process. Threads let the tester call the system on the
same host while it is handling a run.
"""
import json

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wrappers import Response

from app import app as system_app
from test_runner.service import app as tester_app


def index(environ, start_response):
    if environ.get("PATH_INFO", "/") not in ("", "/"):
        response = Response(json.dumps({"message": "Not found", "source": "system"}), status=404,
                            mimetype="application/json")
    else:
        response = Response(json.dumps({"status": "ok", "system": "/system", "tester": "/tester"}),
                            mimetype="application/json")
    return response(environ, start_response)


# Caddy terminates HTTPS and forwards to gunicorn on 127.0.0.1, so trust one proxy's
# X-Forwarded-* headers. That makes download_url come out as https://<host>/system/...
application = ProxyFix(DispatcherMiddleware(index, {"/system": system_app, "/tester": tester_app}),
                       x_for=1, x_proto=1, x_host=1)
