"""A company's connections, products, releases and bundles (Phase 5 plan, §9, step 5.3).

    GET  /app/connections                          list; admins add one per connector type
    POST /app/connections/new/<type>               add (the token goes straight to the secret store)
    GET  /app/connections/<conn_id>                its settings; admins edit or delete
    POST /app/connections/<conn_id>                edit (blank token keeps the stored one)
    POST /app/connections/<conn_id>/delete         delete, if nothing uses it
    GET  /app/products                             list; admins create
    POST /app/products                             create
    GET  /app/products/<product>                   its rules and releases, and a bundle request form
    POST /app/products/<product>/delete            delete it and its releases
    POST /app/products/<product>/releases          pin a new release
    GET  /app/products/<product>/releases/<rel>    its pins
    POST /app/products/<product>/releases/<rel>/delete
    GET  /app/products/<product>/entry/<category>  edit a rule (or, with ?release=, a pin)
    POST /app/products/<product>/entry/<category>  save it;  .../clear removes it
    POST /app/bundles                              request a bundle for the active organization
    GET  /app/bundles/<bundle_id>                  its status, manifest and download link

These routes sit on account_pages' blueprint, so they share its sign-in, CSRF and
active-organization checks. Members can read everything and request bundles; only admins change
anything (org_sources.py enforces it).
"""
from flask import flash, g, redirect, render_template, request, url_for

from account_config import CATEGORIES
from account_pages import _need_org, _refused, _state, bp
from accounts import AccountError
from connectors import REGISTRY
from org_sources import COMPANY_TYPES, MAX_CONNECTIONS, describe_ref, ref_form_values

TERMINAL = ("COMPLETED", "PARTIAL_SUCCESS", "FAILED")


def _sources():
    return _state().sources


def _admin() -> bool:
    return g.member is not None and g.member.role == "admin"


def _fail(error: AccountError, endpoint: str, **values):
    flash(str(error), "error")
    return redirect(url_for(endpoint, **values))


# Connections

@bp.route("/connections", methods=["GET"])
def connections():
    if (answer := _need_org()) is not None:
        return answer
    conns = _sources().connections(g.org.org_id, g.sub)
    return render_template("account/connections.html", title="Connections", connections=conns,
                           registry=REGISTRY, types=COMPANY_TYPES, is_admin=_admin(),
                           max_connections=MAX_CONNECTIONS)


@bp.route("/connections/new/<conn_type>", methods=["POST"])
def add_connection(conn_type):
    if (answer := _need_org()) is not None:
        return answer
    try:
        conn = _sources().add_connection(g.org.org_id, g.sub, conn_type, request.form)
    except AccountError as error:
        return _refused(error, "account.connections")
    flash(f"Added the {REGISTRY[conn.type].display_name} connection.", "ok")
    return redirect(url_for("account.connections"))


@bp.route("/connections/<conn_id>", methods=["GET"])
def connection(conn_id):
    if (answer := _need_org()) is not None:
        return answer
    try:
        conn = _sources().connection(g.org.org_id, g.sub, conn_id)
    except AccountError as error:
        return _refused(error, "account.connections")
    connector = REGISTRY[conn.type]
    return render_template("account/connection.html", title=f"{connector.display_name} connection", conn=conn,
                           connector=connector, usage=_sources().usage(g.org.org_id, conn.conn_id),
                           is_admin=_admin(), values=ref_form_values(conn.settings))


@bp.route("/connections/<conn_id>", methods=["POST"])
def edit_connection(conn_id):
    if (answer := _need_org()) is not None:
        return answer
    try:
        _sources().edit_connection(g.org.org_id, g.sub, conn_id, request.form)
    except AccountError as error:
        if error.status == 404:
            return _refused(error, "account.connections")
        return _fail(error, "account.connection", conn_id=conn_id)
    flash("Connection saved.", "ok")
    return redirect(url_for("account.connection", conn_id=conn_id))


@bp.route("/connections/<conn_id>/delete", methods=["POST"])
def delete_connection(conn_id):
    if (answer := _need_org()) is not None:
        return answer
    try:
        _sources().delete_connection(g.org.org_id, g.sub, conn_id)
    except AccountError as error:
        if error.status == 404:
            return _refused(error, "account.connections")
        return _fail(error, "account.connection", conn_id=conn_id)
    flash("Connection and its token deleted.", "ok")
    return redirect(url_for("account.connections"))


# Products and releases

def _conn_names():
    """conn_id -> a readable name, for showing rules and pins."""
    names = {}
    for conn in _sources().connections(g.org.org_id, g.sub):
        connector = REGISTRY[conn.type]
        detail = conn.settings.get("repository") or conn.settings.get("url") or conn.settings.get("cloud_id") or ""
        names[conn.conn_id] = f"{connector.display_name} {detail}".strip()
    return names


def _rows(entries):
    names = _conn_names()
    return [{"category": c, "entry": entries.get(c),
             "conn": names.get(entries[c].get("conn"), "a deleted connection") if c in entries else None,
             "ref": describe_ref(entries[c]) if c in entries else ""} for c in CATEGORIES]


@bp.route("/products", methods=["GET"])
def products():
    if (answer := _need_org()) is not None:
        return answer
    return render_template("account/products.html", title="Products",
                           products=_sources().products(g.org.org_id, g.sub), is_admin=_admin())


@bp.route("/products", methods=["POST"])
def create_product():
    if (answer := _need_org()) is not None:
        return answer
    try:
        product = _sources().create_product(g.org.org_id, g.sub, request.form.get("product_id", ""))
    except AccountError as error:
        return _refused(error, "account.products")
    return redirect(url_for("account.product", product_id=product.product_id))


@bp.route("/products/<product_id>", methods=["GET"])
def product(product_id):
    if (answer := _need_org()) is not None:
        return answer
    sources = _sources()
    try:
        found = sources.product(g.org.org_id, g.sub, product_id)
        releases = sources.releases(g.org.org_id, g.sub, product_id)
    except AccountError as error:
        return _refused(error, "account.products")
    return render_template("account/product.html", title=f"Product {found.product_id}", product=found,
                           rows=_rows(found.rules), releases=releases, is_admin=_admin(), release=None)


@bp.route("/products/<product_id>/delete", methods=["POST"])
def delete_product(product_id):
    if (answer := _need_org()) is not None:
        return answer
    try:
        _sources().delete_product(g.org.org_id, g.sub, product_id)
    except AccountError as error:
        return _refused(error, "account.products")
    flash(f"Deleted product {product_id} and its releases.", "ok")
    return redirect(url_for("account.products"))


@bp.route("/products/<product_id>/releases", methods=["POST"])
def create_release(product_id):
    if (answer := _need_org()) is not None:
        return answer
    try:
        created = _sources().create_release(g.org.org_id, g.sub, product_id, request.form.get("release", ""))
    except AccountError as error:
        if error.status == 404:
            return _refused(error, "account.products")
        return _fail(error, "account.product", product_id=product_id)
    return redirect(url_for("account.release", product_id=product_id, release=created.release))


@bp.route("/products/<product_id>/releases/<release>", methods=["GET"])
def release(product_id, release):
    if (answer := _need_org()) is not None:
        return answer
    try:
        found = _sources().release(g.org.org_id, g.sub, product_id, release)
        product = _sources().product(g.org.org_id, g.sub, product_id)
    except AccountError as error:
        return _refused(error, "account.products")
    rows = _rows(found.pins)
    for row in rows:
        row["inherited"] = row["entry"] is None and row["category"] in product.rules
    return render_template("account/product.html", title=f"{product_id} {found.release}", product=product,
                           rows=rows, releases=[], is_admin=_admin(), release=found.release)


@bp.route("/products/<product_id>/releases/<release>/delete", methods=["POST"])
def delete_release(product_id, release):
    if (answer := _need_org()) is not None:
        return answer
    try:
        _sources().delete_release(g.org.org_id, g.sub, product_id, release)
    except AccountError as error:
        return _fail(error, "account.product", product_id=product_id)
    flash(f"Deleted the pins for {release}. Its bundles now follow the product's rules.", "ok")
    return redirect(url_for("account.product", product_id=product_id))


def _back(product_id, release):
    if release:
        return redirect(url_for("account.release", product_id=product_id, release=release))
    return redirect(url_for("account.product", product_id=product_id))


@bp.route("/products/<product_id>/entry/<category>", methods=["GET"])
def entry(product_id, category):
    if (answer := _need_org()) is not None:
        return answer
    release = request.args.get("release") or None
    sources = _sources()
    try:
        if category not in CATEGORIES:
            raise AccountError("That isn't a category.", 404)
        if release:
            current = sources.release(g.org.org_id, g.sub, product_id, release).pins.get(category)
        else:
            current = sources.product(g.org.org_id, g.sub, product_id).rules.get(category)
        conns = sources.connections(g.org.org_id, g.sub)
    except AccountError as error:
        return _refused(error, "account.products")
    chosen = request.args.get("conn") or (current or {}).get("conn")
    conn = next((c for c in conns if c.conn_id == chosen), None)
    values = ref_form_values(current) if current and conn and current.get("conn") == conn.conn_id else {}
    return render_template("account/entry.html", title=f"{category} for {product_id} {release or '(all releases)'}",
                           product_id=product_id, release=release, category=category, conns=conns, conn=conn,
                           connector=REGISTRY[conn.type] if conn else None, values=values, registry=REGISTRY,
                           is_admin=_admin(), names=_conn_names())


@bp.route("/products/<product_id>/entry/<category>", methods=["POST"])
def save_entry(product_id, category):
    if (answer := _need_org()) is not None:
        return answer
    release = request.form.get("release") or None
    try:
        _sources().set_entry(g.org.org_id, g.sub, product_id, release, category, request.form.get("conn"),
                             request.form)
    except AccountError as error:
        flash(str(error), "error")
        return redirect(url_for("account.entry", product_id=product_id, category=category,
                                release=release, conn=request.form.get("conn")))
    flash(f"Saved {category}.", "ok")
    return _back(product_id, release)


@bp.route("/products/<product_id>/entry/<category>/clear", methods=["POST"])
def clear_entry(product_id, category):
    if (answer := _need_org()) is not None:
        return answer
    release = request.form.get("release") or None
    try:
        _sources().clear_entry(g.org.org_id, g.sub, product_id, release, category)
    except AccountError as error:
        flash(str(error), "error")
    return _back(product_id, release)


# Bundles

@bp.route("/bundles", methods=["POST"])
def request_bundle():
    if (answer := _need_org()) is not None:
        return answer
    from app import submit_bundle  # app.py imports this module's blueprint, so import it late
    product_id = request.form.get("product_id", "").strip()
    release = request.form.get("release_version", "").strip()
    if not product_id or not release:
        flash("Enter a product and a release.", "error")
        return redirect(request.referrer or url_for("account.products"))
    bundle = submit_bundle(product_id, release, g.org.org_id)
    return redirect(url_for("account.bundle", bundle_id=bundle["bundle_id"]))


@bp.route("/bundles/<bundle_id>", methods=["GET"])
def bundle(bundle_id):
    if (answer := _need_org()) is not None:
        return answer
    from app import bundles, may_read, public
    from bundle_service import archives
    found = bundles.get(bundle_id)
    if found is None or not may_read(found) or found.get("_org_id") is None:
        return _refused(AccountError("That bundle doesn't exist or has expired (bundles last an hour).", 404),
                        "account.products")
    shown = public(found)
    return render_template("account/bundle.html", title=f"Bundle for {shown['product_id']} {shown['release_version']}",
                           bundle=shown, done=shown["status"] in TERMINAL,
                           has_archive=archives.get(bundle_id) is not None,
                           other_org=found.get("_org_id") != g.org.org_id)
