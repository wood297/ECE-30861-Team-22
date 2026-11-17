"""Connection tokens and the app's own secrets (Phase 5 plan, §2, step 1.3).

Secrets live in SSM Parameter Store as SecureString parameters, encrypted with the AWS-managed
`aws/ssm` key (step 0.1 confirmed no KMS permissions are needed):

    <prefix>/orgs/<org_id>/conn/<conn_id>    a connection's token
    <prefix>/app/<name>                      our own secrets, e.g. flask-secret-key

`prefix` is "/team22-cra" in production and "/team22-cra-dev" for the -dev resources.

A secret comes back as a `Secret`, whose repr and str never show the value, so it can't leak
through a log line, an error or a debug print. Only `reveal()` gives the value, and only a
connector, about to send it, should call it.
"""
import json
import os
import re
import stat
import threading
from typing import Any, Protocol

from tenant_store import check_uuid

# SSM standard parameters hold at most 4 KB
MAX_SECRET_BYTES = 4096
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}(/[a-z0-9][a-z0-9-]{0,62}){0,3}")
_PREFIX = re.compile(r"(/[a-z0-9][a-z0-9-]{0,62}){1,3}")


class SecretStoreError(Exception):
    pass


class SecretNotFound(SecretStoreError):
    pass


class Secret:
    """A secret value that can't be printed by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(****)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self):
        raise TypeError("Secret values can't be pickled")


def _check_value(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a secret must be a non-empty string")
    if len(value.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError(f"a secret must be at most {MAX_SECRET_BYTES} bytes")
    return value


def check_prefix(prefix: str) -> str:
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise ValueError("prefix must look like /team22-cra")
    return prefix


class SecretStore(Protocol):
    prefix: str

    def connection_path(self, org_id: str, conn_id: str) -> str: ...
    def put_connection_secret(self, org_id: str, conn_id: str, value: str) -> str: ...
    def get_connection_secret(self, org_id: str, conn_id: str) -> Secret: ...
    def delete_connection_secret(self, org_id: str, conn_id: str) -> None: ...
    def delete_org_secrets(self, org_id: str) -> int: ...
    def get_app_secret(self, name: str) -> Secret: ...
    def put_app_secret(self, name: str, value: str) -> None: ...


class _Paths:
    prefix: str

    def connection_path(self, org_id: str, conn_id: str) -> str:
        return f"{self._org_path(org_id)}/conn/{check_uuid(conn_id, 'conn_id')}"

    def _org_path(self, org_id: str) -> str:
        return f"{self.prefix}/orgs/{check_uuid(org_id, 'org_id')}"

    def _app_path(self, name: str) -> str:
        if not isinstance(name, str) or not _APP_NAME.fullmatch(name):
            raise ValueError("app secret names are lower-case words separated by '-' or '/'")
        return f"{self.prefix}/app/{name}"


class InMemorySecretStore(_Paths):
    """Same behavior as SsmSecretStore, kept in a dict. For tests and local runs without AWS."""

    def __init__(self, prefix: str = "/team22-cra-test"):
        self.prefix = check_prefix(prefix)
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def put_connection_secret(self, org_id: str, conn_id: str, value: str) -> str:
        path = self.connection_path(org_id, conn_id)
        with self._lock:
            self._values[path] = _check_value(value)
        return path

    def get_connection_secret(self, org_id: str, conn_id: str) -> Secret:
        return self._get(self.connection_path(org_id, conn_id))

    def delete_connection_secret(self, org_id: str, conn_id: str) -> None:
        with self._lock:
            self._values.pop(self.connection_path(org_id, conn_id), None)

    def delete_org_secrets(self, org_id: str) -> int:
        base = self._org_path(org_id) + "/"
        with self._lock:
            paths = [path for path in self._values if path.startswith(base)]
            for path in paths:
                del self._values[path]
        return len(paths)

    def get_app_secret(self, name: str) -> Secret:
        return self._get(self._app_path(name))

    def put_app_secret(self, name: str, value: str) -> None:
        with self._lock:
            self._values[self._app_path(name)] = _check_value(value)

    def _get(self, path: str) -> Secret:
        with self._lock:
            if path not in self._values:
                raise SecretNotFound("secret not found")
            return Secret(self._values[path])


class LocalFileSecretStore(_Paths):
    """Read-only secrets from a local JSON file, for connector authors on their laptops
    (CRA_LOCAL_SECRETS, Phase 5 plan §11.3). Development only: the server never sets it.

        {"connections": {"<conn_id>": "<token>"}, "app": {"<name>": "<value>"}}

    The file must not be readable by other users, and must never be committed (.gitignore
    covers *.secrets.json). Any organization reads the same connection secrets: locally there
    is only one company.
    """

    def __init__(self, path: str, prefix: str = "/team22-cra-local"):
        self.prefix = check_prefix(prefix)
        if os.name == "posix" and os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretStoreError(f"{path} is readable by other users; run: chmod 600 {path}")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise SecretStoreError(f"{path} must hold a JSON object")
        self._connections = {check_uuid(k, "conn_id"): _check_value(v)
                             for k, v in (data.get("connections") or {}).items()}
        self._app = {k: _check_value(v) for k, v in (data.get("app") or {}).items()}
        for name in self._app:
            self._app_path(name)

    def get_connection_secret(self, org_id: str, conn_id: str) -> Secret:
        self.connection_path(org_id, conn_id)   # same id checks as the real stores
        if conn_id not in self._connections:
            raise SecretNotFound("secret not found")
        return Secret(self._connections[conn_id])

    def get_app_secret(self, name: str) -> Secret:
        self._app_path(name)
        if name not in self._app:
            raise SecretNotFound("secret not found")
        return Secret(self._app[name])

    def put_connection_secret(self, org_id: str, conn_id: str, value: str) -> str:
        raise SecretStoreError("local secrets are read-only; edit the CRA_LOCAL_SECRETS file")

    def put_app_secret(self, name: str, value: str) -> None:
        raise SecretStoreError("local secrets are read-only; edit the CRA_LOCAL_SECRETS file")

    def delete_connection_secret(self, org_id: str, conn_id: str) -> None:
        raise SecretStoreError("local secrets are read-only; edit the CRA_LOCAL_SECRETS file")

    def delete_org_secrets(self, org_id: str) -> int:
        raise SecretStoreError("local secrets are read-only; edit the CRA_LOCAL_SECRETS file")


class SsmSecretStore(_Paths):
    """SecretStore on SSM Parameter Store SecureString parameters."""

    def __init__(self, prefix: str, client: Any = None, region: str | None = None):
        self.prefix = check_prefix(prefix)
        if client is None:
            import boto3
            client = boto3.client("ssm", region_name=region)
        self._client = client

    def put_connection_secret(self, org_id: str, conn_id: str, value: str) -> str:
        path = self.connection_path(org_id, conn_id)
        self._put(path, value)
        return path

    def get_connection_secret(self, org_id: str, conn_id: str) -> Secret:
        return self._get(self.connection_path(org_id, conn_id))

    def delete_connection_secret(self, org_id: str, conn_id: str) -> None:
        try:
            self._client.delete_parameter(Name=self.connection_path(org_id, conn_id))
        except self._client.exceptions.ParameterNotFound:
            pass

    def delete_org_secrets(self, org_id: str) -> int:
        paths: list[str] = []
        kwargs: dict[str, Any] = {"Path": self._org_path(org_id), "Recursive": True, "WithDecryption": False}
        while True:
            response = self._client.get_parameters_by_path(**kwargs)
            paths.extend(parameter["Name"] for parameter in response.get("Parameters", []))
            if not response.get("NextToken"):
                break
            kwargs["NextToken"] = response["NextToken"]
        for start in range(0, len(paths), 10):
            self._client.delete_parameters(Names=paths[start:start + 10])
        return len(paths)

    def get_app_secret(self, name: str) -> Secret:
        return self._get(self._app_path(name))

    def put_app_secret(self, name: str, value: str) -> None:
        self._put(self._app_path(name), value)

    def _put(self, path: str, value: str) -> None:
        try:
            self._client.put_parameter(Name=path, Value=_check_value(value), Type="SecureString",
                                       Tier="Standard", Overwrite=True)
        except self._client.exceptions.ClientError as error:
            # The error never includes the value, but say only what failed, in case that changes
            code = error.response.get("Error", {}).get("Code", "error")
            raise SecretStoreError(f"could not store the secret ({code})") from None

    def _get(self, path: str) -> Secret:
        try:
            response = self._client.get_parameter(Name=path, WithDecryption=True)
        except self._client.exceptions.ParameterNotFound:
            raise SecretNotFound("secret not found") from None
        return Secret(response["Parameter"]["Value"])
