"""Organizations, members, invites and roles (Phase 5 plan, §3 and §6, step 5.2).

The rules about *who may* do what live here; tenant_store.py only keeps the data consistent.
Every method takes the acting person's `sub` and checks their MEMBER record itself, so an
`org_id` from a request is never trusted on its own.

- Anyone signed in can create an organization (at most MAX_ORGS_CREATED) and becomes its admin.
- Admins invite people by email. An invite lasts INVITE_DAYS and can be accepted only by someone
  signed in with that email, verified by Cognito. An email domain never grants membership.
- An organization has at most MAX_MEMBERS people, counting pending invites.
- The last admin can't leave, be removed or be demoted, so every organization has someone who
  can manage it.

Checks and the writes they guard run under one lock. That's correct with our single gunicorn
worker (wsgi.py), the same assumption the rate limits make (§6).
"""
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from expiring_store import ExpiringStore
from tenant_store import (AlreadyExists, Invite, Membership, NotFound, Organization, TenantStore,
                          check_role, check_sub, check_uuid, normalize_email)

MAX_ORGS_CREATED = 3
MAX_MEMBERS = 25
INVITE_DAYS = 7
MAX_EMAIL_LENGTH = 254


class AccountError(Exception):
    """A refused action. The message is shown to the person, so it never holds anything secret."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _not_found() -> AccountError:
    # The same answer whether the organization doesn't exist or you aren't in it, so IDs can't be probed
    return AccountError("That organization doesn't exist or you aren't a member.", 404)


def check_email(email: str) -> str:
    try:
        email = normalize_email(email)
    except ValueError:
        raise AccountError("Enter an email address.") from None
    local, _, domain = email.rpartition("@")
    if not local or "." not in domain or len(email) > MAX_EMAIL_LENGTH or not email.isprintable():
        raise AccountError("Enter an email address.")
    return email


@dataclass(frozen=True)
class MyOrg:
    org: Organization
    role: str


@dataclass(frozen=True)
class MyInvite:
    invite: Invite
    org: Organization


class Accounts:
    def __init__(self, store: TenantStore, clock: Callable[[], float] = time.time):
        self.store = store
        self._clock = clock
        self._lock = threading.Lock()

    # Reading

    def member(self, org_id: Any, sub: str) -> Membership | None:
        """Your MEMBER record in an organization, or None (also for a malformed org_id)."""
        try:
            check_uuid(org_id, "org_id")
        except ValueError:
            return None
        return self.store.get_member(org_id, sub)

    def require(self, org_id: Any, sub: str, admin: bool = False) -> Membership:
        member = self.member(org_id, sub)
        if member is None:
            raise _not_found()
        if admin and member.role != "admin":
            raise AccountError("Only an admin of this organization can do that.", 403)
        return member

    def my_orgs(self, sub: str) -> list[MyOrg]:
        found = []
        for membership in self.store.list_user_orgs(sub):
            org = self.store.get_org(membership.org_id)
            if org is not None:
                found.append(MyOrg(org, membership.role))
        return sorted(found, key=lambda m: (m.org.name.lower(), m.org.org_id))

    def can_create(self, sub: str) -> int:
        """How many more organizations this person may create."""
        return max(0, MAX_ORGS_CREATED - self.store.count_orgs_created_by(sub))

    def my_invites(self, email: str | None) -> list[MyInvite]:
        """Pending invites for a *verified* email; None (unverified or unknown) has none."""
        if not email:
            return []
        found = []
        for invite in self.store.invites_for_email(email):
            org = self.store.get_org(invite.org_id)
            if org is not None:
                found.append(MyInvite(invite, org))
        return sorted(found, key=lambda i: i.org.name.lower())

    def members(self, org_id: str, actor: str) -> list[Membership]:
        self.require(org_id, actor)
        return self.store.list_members(org_id)

    def invites(self, org_id: str, actor: str) -> list[Invite]:
        self.require(org_id, actor, admin=True)
        return self.store.list_invites(org_id)

    # Organizations

    def create_org(self, sub: str, name: str) -> Organization:
        check_sub(sub)
        with self._lock:
            if self.can_create(sub) == 0:
                raise AccountError(f"You can create at most {MAX_ORGS_CREATED} organizations.", 403)
            try:
                return self.store.create_org(name, sub)
            except ValueError:
                raise AccountError("An organization name is 1 to 100 printable characters.") from None

    def rename_org(self, org_id: str, actor: str, name: str) -> Organization:
        with self._lock:
            self.require(org_id, actor, admin=True)
            try:
                return self.store.rename_org(org_id, name)
            except ValueError:
                raise AccountError("An organization name is 1 to 100 printable characters.") from None

    # Invites

    def invite(self, org_id: str, actor: str, email: str, role: str) -> Invite:
        email = check_email(email)
        try:
            check_role(role)
        except ValueError:
            raise AccountError("Choose admin or member.") from None
        with self._lock:
            self.require(org_id, actor, admin=True)
            pending = {i.email for i in self.store.list_invites(org_id)}
            # Inviting the same email again just renews it, so it doesn't count twice
            if email not in pending and len(self.store.list_members(org_id)) + len(pending) >= MAX_MEMBERS:
                raise AccountError(f"An organization can have at most {MAX_MEMBERS} people, "
                                   "counting pending invites.", 403)
            expires = int(self._clock()) + INVITE_DAYS * 24 * 3600
            return self.store.create_invite(org_id, email, role, actor, expires)

    def cancel_invite(self, org_id: str, actor: str, email: str) -> None:
        email = check_email(email)
        with self._lock:
            self.require(org_id, actor, admin=True)
            self.store.delete_invite(org_id, email)

    def accept_invite(self, org_id: Any, sub: str, email: str | None) -> Membership:
        """Join with a pending invite for your verified email."""
        if not email:
            raise AccountError("Your email address isn't verified, so you can't accept invites.", 403)
        try:
            check_uuid(org_id, "org_id")
        except ValueError:
            raise AccountError("That invite doesn't exist or has expired.", 404) from None
        with self._lock:
            if self.store.get_member(org_id, sub) is not None:
                self.store.delete_invite(org_id, email)
                raise AccountError("You're already a member of that organization.", 409)
            if len(self.store.list_members(org_id)) >= MAX_MEMBERS:
                raise AccountError(f"That organization already has {MAX_MEMBERS} people.", 403)
            try:
                return self.store.accept_invite(org_id, email, sub)
            except NotFound:
                raise AccountError("That invite doesn't exist or has expired.", 404) from None
            except AlreadyExists:
                raise AccountError("You're already a member of that organization.", 409) from None

    def decline_invite(self, org_id: Any, email: str | None) -> None:
        if not email:
            return
        try:
            check_uuid(org_id, "org_id")
        except ValueError:
            return
        with self._lock:
            self.store.delete_invite(org_id, email)

    # Members

    def _admins(self, org_id: str) -> list[str]:
        return [m.sub for m in self.store.list_members(org_id) if m.role == "admin"]

    def set_role(self, org_id: str, actor: str, sub: str, role: str) -> Membership:
        try:
            check_role(role)
        except ValueError:
            raise AccountError("Choose admin or member.") from None
        with self._lock:
            self.require(org_id, actor, admin=True)
            target = self.member(org_id, sub)
            if target is None:
                raise AccountError("That person isn't a member.", 404)
            if target.role == "admin" and role != "admin" and self._admins(org_id) == [sub]:
                raise AccountError("The last admin can't be made a member. Make someone else an admin first.", 409)
            return self.store.set_role(org_id, sub, role)

    def remove_member(self, org_id: str, actor: str, sub: str) -> None:
        """An admin removes someone, or anyone removes themselves (leaving)."""
        with self._lock:
            self.require(org_id, actor, admin=actor != sub)
            target = self.member(org_id, sub)
            if target is None:
                raise AccountError("That person isn't a member.", 404)
            if target.role == "admin" and self._admins(org_id) == [sub]:
                raise AccountError("The last admin can't leave or be removed. Make someone else an admin "
                                   "first, or delete the organization.", 409)
            self.store.remove_member(org_id, sub)


class UserDirectory(Protocol):
    def describe(self, sub: str) -> dict[str, str] | None:
        """{"email": ..., "name": ...} for a Cognito user, or None if unknown or unreachable."""


class NoDirectory:
    def describe(self, sub: str) -> dict[str, str] | None:
        return None


class CognitoDirectory:
    """Names and emails live only in Cognito (§1), so the members page looks them up here.
    Answers are cached for a few minutes; a failed lookup shows the person as unknown."""

    def __init__(self, pool_id: str, region: str, client: Any = None, ttl: float = 300):
        self.pool_id = pool_id
        self._region = region
        self._client = client
        self._cache: ExpiringStore[str, dict[str, str]] = ExpiringStore(ttl, 1000)

    def describe(self, sub: str) -> dict[str, str] | None:
        cached = self._cache.get(sub)
        if cached is not None:
            return cached
        try:
            if self._client is None:
                import boto3
                self._client = boto3.client("cognito-idp", region_name=self._region)
            user = self._client.admin_get_user(UserPoolId=self.pool_id, Username=sub)
        except Exception:  # unknown user, throttling, no permission: show the person as unknown
            return None
        attributes = {a["Name"]: a["Value"] for a in user.get("UserAttributes", [])}
        found = {"email": attributes.get("email", ""), "name": attributes.get("name", "")}
        self._cache[sub] = found
        return found
