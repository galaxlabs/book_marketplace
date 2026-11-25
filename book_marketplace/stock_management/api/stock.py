import frappe
from frappe import _


def _assert_branch_access(branch_id):
    """Only the business that owns this branch (or an admin) may see what is kept there."""
    profile = frappe.db.get_value("BranchLocation", branch_id, "business_profile")
    if not profile:
        frappe.throw(_("This storage place was not found."), frappe.DoesNotExistError)
    _assert_publisher_profile(profile)
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
    doc = frappe.get_doc({
        "doctype": "StockTransfer",
        "from_branch": from_branch,
        "to_branch": to_branch,
        "book": book,
        "volume": volume,
        "quantity": quantity,
        "status": "Draft",
    })
    doc.insert()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def confirm_receive(transfer_id):
    doc = frappe.get_doc("StockTransfer", transfer_id)
    doc.status = "Received"
    doc.received_on = frappe.utils.today()
    doc.save()
    return {"name": doc.name, "status": doc.status}


# ─── Publisher's own books: recorded quantity ───────────────

def _assert_publisher_profile(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)


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
    _assert_publisher_profile(profile_id)
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
    _assert_publisher_profile(profile_id)
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
    before = frappe.db.get_value("StockLedger", existing, "quantity") if existing else 0
    if existing:
        frappe.db.set_value("StockLedger", existing, "quantity", qty)
    else:
        doc = frappe.get_doc({
            "doctype": "StockLedger",
            "book": book_id,
            "business_profile": profile_id,
            "branch_location": branch,
            "quantity": qty,
            "state": "Available",
        })
        doc.insert(ignore_permissions=True)
    from book_marketplace.trading.api.customer_portal import notify_back_in_stock
    notify_back_in_stock(profile_id, book_id, before, qty)
    return {"book": book_id, "quantity": qty}


@frappe.whitelist()
def set_catalog_quantity(profile_id, book_id, quantity):
    """Quantity of a book this business holds. Allowed when the book is in its catalog, or when it is the publisher's own book."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        frappe.throw(_("Quantity must be a whole number."))
    if qty < 0:
        frappe.throw(_("Quantity cannot be negative."))
    in_catalog = frappe.db.exists("BusinessCatalogItem", {"business_profile": profile_id, "book": book_id})
    is_publisher = frappe.db.get_value("Book", book_id, "publisher") == profile_id
    if not (in_catalog or is_publisher):
        frappe.throw(_("Add this book to your catalog first."), frappe.PermissionError)
    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book_id, "branch_location": branch, "state": "Available"},
        "name",
    )
    before = frappe.db.get_value("StockLedger", existing, "quantity") if existing else 0
    if existing:
        frappe.db.set_value("StockLedger", existing, "quantity", qty)
    else:
        frappe.get_doc({
            "doctype": "StockLedger", "book": book_id, "business_profile": profile_id,
            "branch_location": branch, "quantity": qty, "state": "Available",
        }).insert(ignore_permissions=True)
    from book_marketplace.trading.api.customer_portal import notify_back_in_stock
    notify_back_in_stock(profile_id, book_id, before, qty)
    return {"book": book_id, "quantity": qty}


BRANCH_TYPES = ("Shop", "Godown", "Warehouse", "Van", "Temporary")


@frappe.whitelist()
def my_branches(profile_id):
    """Every branch of the business (shop, godown, warehouse ...), active ones first, with the stock held there."""
    _assert_publisher_profile(profile_id)
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
    _assert_publisher_profile(profile_id)
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
    _assert_publisher_profile(profile_id)
    if frappe.db.get_value("BranchLocation", branch, "business_profile") != profile_id:
        frappe.throw(_("This branch is not yours."), frappe.PermissionError)
    on = 1 if str(active) in ("1", "true", "True") else 0
    if not on and frappe.db.count("BranchLocation", {"business_profile": profile_id, "is_active": 1}) <= 1:
        frappe.throw(_("Keep at least one active branch."))
    frappe.db.set_value("BranchLocation", branch, "is_active", on)
    return {"name": branch, "is_active": on}
