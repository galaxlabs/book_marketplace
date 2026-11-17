import frappe
from frappe import _


@frappe.whitelist()
def get_by_branch(branch_id):
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
    publisher = frappe.db.get_value("Book", book_id, "publisher")
    if publisher != profile_id:
        frappe.throw(_("This book is not published by this business."), frappe.PermissionError)

    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book_id, "branch_location": branch, "state": "Available"},
        "name",
    )
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
    if existing:
        frappe.db.set_value("StockLedger", existing, "quantity", qty)
    else:
        frappe.get_doc({
            "doctype": "StockLedger", "book": book_id, "business_profile": profile_id,
            "branch_location": branch, "quantity": qty, "state": "Available",
        }).insert(ignore_permissions=True)
    return {"book": book_id, "quantity": qty}
