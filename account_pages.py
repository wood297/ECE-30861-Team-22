"""The signed-in pages (Phase 5 plan, §9, step 5.2): organizations, members, invites and roles.

    GET  /app/                                  your organizations, invites, and creating one
    POST /app/orgs                              create an organization (you become its admin)
    POST /app/orgs/<org_id>/switch              make it your active organization
    POST /app/invites/<org_id>/accept|decline   answer an invite for your verified email
    GET  /app/members                           the active organization's members
    POST /app/members/invite                    admin: invite an email as admin or member
    POST /app/members/invites/cancel            admin: withdraw a pending invite
    POST /app/members/<sub>/role                admin: change someone's role
    POST /app/members/<sub>/remove              admin: remove someone
    POST /app/leave                             leave the active organization
    POST /app/org/rename                        admin: rename the active organization

Only browser sessions reach these pages; API callers and the tester get sent to sign in. The
active organization comes from the session but is checked against your MEMBER record on every
request (accounts.py), so a stale or forged org_id in the cookie gets you nothing.
"""
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

import auth
from accounts import AccountError, MAX_MEMBERS, MAX_ORGS_CREATED

bp = Blueprint("account", __name__, url_prefix="/app")


def _state() -> auth.AuthState:
    state = current_app.extensions["cra_auth"]
    return state


@bp.before_request
def _signed_in():
    if current_app.extensions.get("cra_auth") is None:
        return auth.message_page(503, "Accounts aren't available", "Accounts aren't set up on this server.")
    identity: auth.Identity = g.identity
    if identity.is_tester or identity.via != "session":
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        auth.csrf.protect()
    g.sub = identity.sub
    g.email = session.get("email")
    # The active organization, confirmed against the MEMBER record
    g.org = g.member = None
    org_id = session.get("org_id")
    if org_id:
        member = _state().accounts.member(org_id, g.sub)
        org = _state().accounts.store.get_org(org_id) if member else None
        if member is None or org is None:
            session["org_id"] = None
        else:
            g.org, g.member = org, member
    return None


def _refused(error: AccountError, endpoint: str):
    flash(str(error), "error")
    return redirect(url_for(endpoint))


def _date(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


@bp.route("/", methods=["GET"])
def home():
    accounts = _state().accounts
    return render_template("account/home.html", title="Your organizations",
                           orgs=accounts.my_orgs(g.sub), invites=accounts.my_invites(g.email),
                           can_create=accounts.can_create(g.sub), max_orgs=MAX_ORGS_CREATED,
                           email_verified=bool(g.email), date=_date)


@bp.route("/orgs", methods=["POST"])
def create_org():
    try:
        org = _state().accounts.create_org(g.sub, request.form.get("name", ""))
    except AccountError as error:
        return _refused(error, "account.home")
    session["org_id"] = org.org_id
    flash(f"Created {org.name}. You're its admin.", "ok")
    return redirect(url_for("account.members"))


@bp.route("/orgs/<org_id>/switch", methods=["POST"])
def switch_org(org_id):
    accounts = _state().accounts
    try:
        accounts.require(org_id, g.sub)
    except AccountError as error:
        return _refused(error, "account.home")
    session["org_id"] = org_id
    return redirect(url_for("account.members"))


@bp.route("/invites/<org_id>/accept", methods=["POST"])
def accept_invite(org_id):
    try:
        _state().accounts.accept_invite(org_id, g.sub, g.email)
    except AccountError as error:
        return _refused(error, "account.home")
    session["org_id"] = org_id
    flash("You've joined the organization.", "ok")
    return redirect(url_for("account.members"))


@bp.route("/invites/<org_id>/decline", methods=["POST"])
def decline_invite(org_id):
    _state().accounts.decline_invite(org_id, g.email)
    flash("Invite declined.", "ok")
    return redirect(url_for("account.home"))


def _need_org():
    if g.org is None:
        flash("Choose an organization first.", "error")
        return redirect(url_for("account.home"))
    return None


@bp.route("/members", methods=["GET"])
def members():
    if (answer := _need_org()) is not None:
        return answer
    state = _state()
    is_admin = g.member.role == "admin"
    people = []
    for m in state.accounts.members(g.org.org_id, g.sub):
        info = state.directory.describe(m.sub) or {}
        people.append({"sub": m.sub, "role": m.role, "joined": _date(m.joined_at), "you": m.sub == g.sub,
                       "name": info.get("name") or "", "email": info.get("email") or ""})
    people.sort(key=lambda p: (p["role"] != "admin", (p["name"] or p["email"] or p["sub"]).lower()))
    invites = state.accounts.invites(g.org.org_id, g.sub) if is_admin else []
    return render_template("account/members.html", title=g.org.name, people=people, invites=invites,
                           is_admin=is_admin, max_members=MAX_MEMBERS, date=_date)


def _org_action(action, success: str):
    if (answer := _need_org()) is not None:
        return answer
    try:
        action()
    except AccountError as error:
        return _refused(error, "account.members")
    flash(success, "ok")
    return redirect(url_for("account.members"))


@bp.route("/members/invite", methods=["POST"])
def invite():
    email = request.form.get("email", "")
    return _org_action(
        lambda: _state().accounts.invite(g.org.org_id, g.sub, email, request.form.get("role", "member")),
        f"Invited {email.strip().lower()}. They'll see the invite when they sign in with that email "
        "(it lasts 7 days).")


@bp.route("/members/invites/cancel", methods=["POST"])
def cancel_invite():
    return _org_action(lambda: _state().accounts.cancel_invite(g.org.org_id, g.sub, request.form.get("email", "")),
                       "Invite withdrawn.")


@bp.route("/members/<sub>/role", methods=["POST"])
def set_role(sub):
    return _org_action(lambda: _state().accounts.set_role(g.org.org_id, g.sub, sub, request.form.get("role", "")),
                       "Role changed.")


@bp.route("/members/<sub>/remove", methods=["POST"])
def remove_member(sub):
    if (answer := _need_org()) is not None:
        return answer
    if sub == g.sub:
        return leave()
    return _org_action(lambda: _state().accounts.remove_member(g.org.org_id, g.sub, sub), "Member removed.")


@bp.route("/leave", methods=["POST"])
def leave():
    if (answer := _need_org()) is not None:
        return answer
    name = g.org.name
    try:
        _state().accounts.remove_member(g.org.org_id, g.sub, g.sub)
    except AccountError as error:
        return _refused(error, "account.members")
    session["org_id"] = None
    flash(f"You've left {name}.", "ok")
    return redirect(url_for("account.home"))


@bp.route("/org/rename", methods=["POST"])
def rename_org():
    return _org_action(lambda: _state().accounts.rename_org(g.org.org_id, g.sub, request.form.get("name", "")),
                       "Organization renamed.")
