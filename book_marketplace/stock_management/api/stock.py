import frappe
from frappe import _


def _assert_branch_access(branch_id, permission="stock.view"):
    """Only the business that owns this branch (the owner, a team member with the permission, or an admin) may use it."""
    profile = frappe.db.get_value("BranchLocation", branch_id, "business_profile")
    if not profile:
        frappe.throw(_("This storage place was not found."), frappe.DoesNotExistError)
    _assert_publisher_profile(profile, permission)
    return profile


@frappe.whitelist()
def get_by_branch(branch_id):
    _assert_branch_access(branch_id)
    entries = frappe.get_all(
        "StockLedger",
        filters={"branch_location": branch_id},
        fields=["name", "book", "volume", "quantity", "state", "reference_type", "reference_name"],
        order_by="modified desc",
    )
    for e in entries:
        book = frappe.db.get_value("Book", e.book, ["title", "author", "book_type"], as_dict=True)
        e.book_title = book.title if book else ""
        e.book_author = book.author if book else ""
    return entries


@frappe.whitelist()
def get_summary(profile_id):
    _assert_publisher_profile(profile_id, "stock.view")
    locations = frappe.get_all("BranchLocation", filters={"business_profile": profile_id, "is_active": 1}, pluck="name")
    if not locations:
        return []
    result = []
    for loc in locations:
        available = sum(
            frappe.get_all(
                "StockLedger",
                filters={"branch_location": loc, "state": "Available"},
                pluck="quantity",
            )
        )
        reserved = sum(
            frappe.get_all(
                "StockLedger",
                filters={"branch_location": loc, "state": "Reserved"},
                pluck="quantity",
            )
        )
        loc_name = frappe.db.get_value("BranchLocation", loc, "branch_name")
        result.append({"branch_id": loc, "branch_name": loc_name, "available": available, "reserved": reserved})
    return result


@frappe.whitelist()
def get_volume_stock(book_id, profile_id=None):
    from book_marketplace.auth.permissions import assert_access
    if profile_id:
        assert_access(profile_id, "stock.view")
    filters = {"book": book_id, "state": "Available"}
    if profile_id:
        filters["business_profile"] = profile_id
    entries = frappe.get_all(
        "StockLedger",
        filters=filters,
        fields=["volume", "quantity", "branch_location"],
    )
    volume_stock = {}
    for e in entries:
        vol = e.volume or "Complete Set"
        if vol not in volume_stock:
            volume_stock[vol] = 0
        volume_stock[vol] += e.quantity
    return volume_stock


@frappe.whitelist()
def transfer(from_branch, to_branch, book, quantity, volume=None):
    """Move copies from one storage place to another of the same business. The stock moves when the transfer is confirmed."""
    qty = int(quantity or 0)
    if qty <= 0:
        frappe.throw(_("Quantity must be more than zero."))
    if from_branch == to_branch:
        frappe.throw(_("Choose two different storage places."))
    p1 = _assert_branch_access(from_branch, "stock.edit")
    p2 = _assert_branch_access(to_branch, "stock.edit")
    if p1 != p2:
        frappe.throw(_("Both storage places must belong to the same business."), frappe.PermissionError)
    doc = frappe.get_doc({
        "doctype": "StockTransfer",
        "from_branch": from_branch,
        "to_branch": to_branch,
        "book": book,
        "volume": volume,
        "quantity": qty,
        "status": "Draft",
    })
    doc.insert()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def confirm_receive(transfer_id):
    """The copies leave the first place and arrive at the second, and both sides get a history line."""
    from book_marketplace.stock_management import movement
    doc = frappe.get_doc("StockTransfer", transfer_id)
    profile = _assert_branch_access(doc.from_branch, "stock.edit")
    _assert_branch_access(doc.to_branch, "stock.edit")
    if doc.status in ("Received", "Cancelled"):
        frappe.throw(_("This transfer is already {0}.").format(doc.status))
    qty = int(doc.quantity or 0)
    key = {"business_profile": profile, "book": doc.book, "state": "Available", "branch_location": doc.from_branch}
    if doc.volume:
        key["volume"] = doc.volume
    src = frappe.db.get_value("StockLedger", key, "name")
    have = int(frappe.db.get_value("StockLedger", src, "quantity") or 0) if src else 0
    if have < qty:
        frappe.throw(_("Only {0} copies are available at the first place.").format(have))
    movement.change_qty(src, -qty, "transfer_out", "StockTransfer", doc.name)
    dkey = {**key, "branch_location": doc.to_branch}
    dst = frappe.db.get_value("StockLedger", dkey, "name")
    if dst:
        movement.change_qty(dst, qty, "transfer_in", "StockTransfer", doc.name)
    else:
        movement.add_row(doc.book, profile, doc.to_branch, qty, "Available", "transfer_in", "StockTransfer", doc.name, volume=doc.volume)
    doc.status = "Received"
    doc.received_on = frappe.utils.today()
    doc.save()
    return {"name": doc.name, "status": doc.status}


# ─── Publisher's own books: recorded quantity ───────────────

def _assert_publisher_profile(profile_id, permission="stock.edit"):
    """The owner or a team member whose role gives this permission (default: change stock)."""
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, permission)


def _default_branch(profile_id):
    name = frappe.db.get_value("BranchLocation", {"business_profile": profile_id, "is_active": 1}, "name")
    if name:
        return name
    doc = frappe.get_doc({
        "doctype": "BranchLocation",
        "business_profile": profile_id,
        "branch_name": "Main",
        "location_type": "Shop",
        "is_active": 1,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def _recorded_quantity(profile_id, book_id):
    """None when no stock record exists for this book (quantity not given). Otherwise the sum of available stock."""
    if not frappe.db.exists("StockLedger", {"business_profile": profile_id, "book": book_id}):
        return None
    rows = frappe.get_all(
        "StockLedger",
        filters={"business_profile": profile_id, "book": book_id, "state": "Available"},
        fields=["quantity"],
    )
    return sum(int(r.quantity or 0) for r in rows)


@frappe.whitelist()
def list_own_books(profile_id):
    _assert_publisher_profile(profile_id, "stock.view")
    books = frappe.get_all(
        "Book",
        filters={"publisher": profile_id, "global_visibility": 1},
        fields=["name", "title", "author", "set_mrp"],
        order_by="title asc",
        limit_page_length=1000,
    )
    for b in books:
        b.quantity = _recorded_quantity(profile_id, b.name)
    return books


@frappe.whitelist()
def set_own_book_quantity(profile_id, book_id, quantity):
    _assert_publisher_profile(profile_id, "stock.edit")
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        frappe.throw(_("Quantity must be a whole number."))
    if qty < 0:
        frappe.throw(_("Quantity cannot be negative."))
    publisher, owner = frappe.db.get_value("Book", book_id, ["publisher", "owner_profile"])
    if profile_id not in (publisher, owner):
        frappe.throw(_("This book is not published or added by this business."), frappe.PermissionError)

    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book_id, "branch_location": branch, "state": "Available"},
        "name",
    )
    from book_marketplace.stock_management import movement
    before = frappe.db.get_value("StockLedger", existing, "quantity") if existing else 0
    if existing:
        movement.set_qty(existing, qty, "manual")
    else:
        movement.add_row(book_id, profile_id, branch, qty, "Available", "manual")
    from book_marketplace.trading.api.customer_portal import notify_back_in_stock
    notify_back_in_stock(profile_id, book_id, before, qty)
    return {"book": book_id, "quantity": qty}


@frappe.whitelist()
def set_catalog_quantity(profile_id, book_id, quantity):
    """Quantity of a book this business holds. Allowed when the book is in its catalog, or when it is the publisher's own book."""
    _assert_publisher_profile(profile_id, "stock.edit")
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        frappe.throw(_("Quantity must be a whole number."))
    if qty < 0:
        frappe.throw(_("Quantity cannot be negative."))
    in_catalog = frappe.db.exists("Business Catalog Item", {"business_profile": profile_id, "book": book_id})
    is_publisher = frappe.db.get_value("Book", book_id, "publisher") == profile_id
    if not (in_catalog or is_publisher):
        frappe.throw(_("Add this book to your catalog first."), frappe.PermissionError)
    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book_id, "branch_location": branch, "state": "Available"},
        "name",
    )
    from book_marketplace.stock_management import movement
    before = frappe.db.get_value("StockLedger", existing, "quantity") if existing else 0
    if existing:
        movement.set_qty(existing, qty, "manual")
    else:
        movement.add_row(book_id, profile_id, branch, qty, "Available", "manual")
    from book_marketplace.trading.api.customer_portal import notify_back_in_stock
    notify_back_in_stock(profile_id, book_id, before, qty)
    return {"book": book_id, "quantity": qty}


@frappe.whitelist()
def location_history(branch, from_date=None, to_date=None, book=None, limit=300):
    """The history of one storage place: how many times goods came in and went out, and each line (newest first).
    Same access as the overview: the owning business or an admin. Dates are optional (YYYY-MM-DD)."""
    _assert_branch_access(branch, "stock.view")
    limit = min(int(limit or 300), 1000)
    where, args = ["branch_location = %(branch)s"], {"branch": branch}
    if from_date:
        where.append("occurred_on >= %(f)s")
        args["f"] = f"{from_date} 00:00:00"
    if to_date:
        where.append("occurred_on <= %(t)s")
        args["t"] = f"{to_date} 23:59:59"
    if book:
        where.append("book = %(book)s")
        args["book"] = book
    cond = " and ".join(where)
    sums = {r.direction: r for r in frappe.db.sql(
        f"select direction, count(*) as events, coalesce(sum(abs(`change`)), 0) as units from `tabStockMovement` where {cond} group by direction",
        args, as_dict=True)}
    pick = lambda d, f: int(sums[d][f]) if d in sums else 0
    rows = frappe.db.sql(
        f"""select m.name, m.occurred_on, m.book, m.volume, m.state, m.`change`, m.qty_before, m.qty_after, m.direction, m.reason,
                   m.reference_type, m.reference_name, m.user from `tabStockMovement` m where {cond.replace("branch_location", "m.branch_location").replace("occurred_on", "m.occurred_on").replace("book = ", "m.book = ")}
            order by m.occurred_on desc, m.name desc limit {limit}""", args, as_dict=True)
    titles = {b.name: b.title for b in frappe.get_all("Book", filters={"name": ["in", list({r.book for r in rows}) or [""]]}, fields=["name", "title"])}
    users = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list({r.user for r in rows if r.user}) or [""]]}, fields=["name", "full_name"])}
    for r in rows:
        r["title"] = titles.get(r.book, r.book)
        r["user_name"] = users.get(r.user, r.user or "")
        r["occurred_on"] = str(r["occurred_on"])
    return {
        "summary": {
            "in_events": pick("In", "events"), "in_units": pick("In", "units"),
            "out_events": pick("Out", "events"), "out_units": pick("Out", "units"),
            "internal_events": pick("Internal", "events"), "opening_units": pick("Opening", "units"),
        },
        "movements": rows,
        "shown": len(rows),
    }


BRANCH_TYPES = ("Shop", "Godown", "Warehouse", "Van", "Temporary")


@frappe.whitelist()
def my_branches(profile_id):
    """Every branch of the business (shop, godown, warehouse ...), active ones first, with the stock held there."""
    _assert_publisher_profile(profile_id, "stock.view")
    rows = frappe.get_all("BranchLocation", filters={"business_profile": profile_id},
                          fields=["name", "branch_name", "location_type", "city", "address", "is_active"],
                          order_by="is_active desc, creation asc", limit_page_length=0)
    business_name = frappe.db.get_value("BusinessProfile", profile_id, "business_name") or ""
    for r in rows:
        r["stock"] = int(frappe.db.sql("""select coalesce(sum(quantity), 0) from `tabStockLedger`
            where branch_location=%s and state='Available'""", r.name)[0][0] or 0)
        r["business_name"] = business_name
    return rows


@frappe.whitelist()
def location_overview(branch):
    """What is kept at one storage place: the page its QR opens. Books with their quantity by state, and the totals.
    Needs a sign-in of the business that owns the place; nobody else sees quantities."""
    profile = _assert_branch_access(branch)
    b = frappe.db.get_value("BranchLocation", branch, ["name", "branch_name", "location_type", "city", "address", "is_active"], as_dict=True)
    b["business_profile"] = profile
    b["business_name"] = frappe.db.get_value("BusinessProfile", profile, "business_name") or ""
    rows = frappe.db.sql("""select book, ifnull(volume, '') as volume, state, sum(quantity) as qty from `tabStockLedger`
        where branch_location=%s group by book, volume, state""", branch, as_dict=True)
    states = {"Available": "available", "Reserved": "reserved", "Incoming": "incoming", "Damaged": "damaged", "In Transit": "in_transit"}
    books = {}
    totals = {v: 0 for v in states.values()}
    for r in rows:
        key = (r.book, r.volume)
        item = books.setdefault(key, {"book": r.book, "volume": r.volume, **{v: 0 for v in states.values()}})
        field = states.get(r.state)
        if field:
            item[field] += int(r.qty or 0)
            totals[field] += int(r.qty or 0)
    names = list({k[0] for k in books})
    info = {x.name: x for x in frappe.get_all("Book", filters={"name": ["in", names or [""]]},
                                              fields=["name", "title", "author", "cover_image"])}
    out = []
    for item in books.values():
        i = info.get(item["book"])
        item["title"] = i.title if i else item["book"]
        item["author"] = (i.author if i else "") or ""
        item["cover_image"] = (i.cover_image if i else "") or ""
        item["total"] = sum(item[v] for v in states.values())
        if item["total"] > 0:
            out.append(item)
    out.sort(key=lambda x: (x["title"], x["volume"]))
    return {"branch": b, "books": out,
            "totals": {"titles": len({x["book"] for x in out}), "lines": len(out), "units": sum(x["total"] for x in out), **totals}}


@frappe.whitelist()
def add_branch(profile_id, branch_name, location_type="Shop", city="", address=""):
    _assert_publisher_profile(profile_id, "locations.manage")
    branch_name = " ".join((branch_name or "").split())[:60]
    if not branch_name:
        frappe.throw(_("Write the branch name."))
    if location_type not in BRANCH_TYPES:
        frappe.throw(_("Unknown branch type."))
    if frappe.db.exists("BranchLocation", {"business_profile": profile_id, "branch_name": branch_name}):
        frappe.throw(_("A branch with this name already exists."))
    doc = frappe.get_doc({"doctype": "BranchLocation", "business_profile": profile_id, "branch_name": branch_name,
                          "location_type": location_type, "city": (city or "").strip()[:80],
                          "address": (address or "").strip()[:300], "is_active": 1}).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def set_branch_active(profile_id, branch, active=1):
    _assert_publisher_profile(profile_id, "locations.manage")
    if frappe.db.get_value("BranchLocation", branch, "business_profile") != profile_id:
        frappe.throw(_("This branch is not yours."), frappe.PermissionError)
    on = 1 if str(active) in ("1", "true", "True") else 0
    if not on and frappe.db.count("BranchLocation", {"business_profile": profile_id, "is_active": 1}) <= 1:
        frappe.throw(_("Keep at least one active branch."))
    frappe.db.set_value("BranchLocation", branch, "is_active", on)
    return {"name": branch, "is_active": on}
