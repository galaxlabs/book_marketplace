import frappe
from frappe import _


@frappe.whitelist()
def create(buyer, seller, items, order_type="Purchase", delivery_method=None):
    from book_marketplace.auth.permissions import assert_access
    assert_access(buyer, "purchases.manage")
    doc = frappe.get_doc({
        "doctype": "InternalOrder",
        "order_type": order_type,
        "buyer_profile": buyer,
        "seller_profile": seller,
        "delivery_method": delivery_method,
        "status": "Draft",
        "items": [
            {
                "book": item["book"],
                "volume": item.get("volume"),
                "quantity": item["quantity"],
                "rate": item["rate"],
                "notes": item.get("notes", ""),
            }
            for item in items
        ],
    })
    doc.insert()
    return {"name": doc.name, "status": doc.status, "total_amount": doc.total_amount}


@frappe.whitelist()
def list_by_profile(profile_id, status=None, limit=20, offset=0):
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, "purchases.view")
    filters = frappe._dict()
    filters.buyer_profile = profile_id
    if status:
        filters.status = status
    orders = frappe.get_all(
        "InternalOrder",
        filters=filters,
        fields=["name", "order_type", "seller_profile", "status", "total_amount", "order_date", "creation"],
        limit_page_length=int(limit),
        limit_start=int(offset),
        order_by="creation desc",
    )
    for o in orders:
        seller = frappe.db.get_value("BusinessProfile", o.seller_profile, "business_name")
        o.seller_name = seller
    return orders


@frappe.whitelist()
def approve(order_id):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Approved"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def reject(order_id, reason=None):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Rejected"
    if reason:
        doc.notes = reason
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def mark_dispatched(order_id, transport_details=None):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Dispatched"
    if transport_details:
        doc.transport_details = transport_details
    doc.save()
    return {"name": doc.name, "status": doc.status}


def _tracked_quantity(seller, book):
    """(ledger name, quantity) for the seller's available stock of a book, or (None, None) when it is not tracked."""
    row = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Available"}, ["name", "quantity"], as_dict=True)
    return (row.name, int(row.quantity or 0)) if row else (None, None)


@frappe.whitelist()
def create_sale(seller, items, customer_name=None, customer_phone=None, payment_status="Paid",
                 discount_percent=0, discount_amount=0):
    """A sale over the counter or to a customer without an account. Checks and reduces the shop's stock."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(seller, "sales.create")
    if isinstance(items, str):
        items = frappe.parse_json(items)
    items = [it for it in (items or []) if it.get("book")]
    if not items:
        frappe.throw(_("Add at least one book."))
    from book_marketplace.catalog.api import item_options as io
    plan = []
    for it in items:
        qty = int(it.get("quantity") or 0)
        if qty <= 0:
            frappe.throw(_("Quantity must be at least 1."))
        # an item this shop sells by size / colour: the size is required and its own stock must cover the sale
        sells_options = io.owner_of(it["book"]) == seller and io.has_options(it["book"])
        it["_label"] = ""
        if it.get("option"):
            if not sells_options:
                frappe.throw(_("This item has no size / colour choice in your shop."))
            opt = io.get_option(it["book"], it["option"])
            if int(opt.stock_qty or 0) < qty:
                frappe.throw(_("Only {0} left of {1}.").format(int(opt.stock_qty or 0), io.label_of(opt)))
            it["_label"] = io.label_of(opt)
        elif sells_options:
            frappe.throw(_("Choose the size / colour of {0}.").format(frappe.db.get_value("Book", it["book"], "title")))
        ledger, have = _tracked_quantity(seller, it["book"])
        if ledger is not None and qty > have:
            title = frappe.db.get_value("Book", it["book"], "title")
            frappe.throw(_("Not enough copies of {0}: you have {1}.").format(title, have))
        plan.append((it, qty, ledger, have))
    doc = frappe.get_doc({
        "doctype": "InternalOrder",
        "order_type": "Sale",
        "seller_profile": seller,
        "customer_name": (customer_name or "").strip() or None,
        "customer_phone": (customer_phone or "").strip() or None,
        "delivery_method": "Pickup",
        "status": "Received",
        "payment_status": payment_status or "Paid",
        "discount_percent": float(discount_percent or 0), "discount_amount": float(discount_amount or 0),
        "items": [
            {"book": it["book"], "quantity": qty, "rate": float(it.get("rate") or 0),
             "item_option": it.get("option") or None, "option_label": it["_label"] or None}
            for it, qty, _l, _h in plan
        ],
    })
    doc.insert(ignore_permissions=True)
    # the same item in two sizes is two lines: the shop stock goes down by each line in turn
    for it, qty, ledger, have in plan:
        if it.get("option"):
            io.take(it["book"], it["option"], qty)
        if ledger is not None:
            from book_marketplace.stock_management import movement
            movement.change_qty(ledger, -qty, "sold", "Order", doc.name)
    return {"name": doc.name, "total_amount": doc.total_amount, "status": doc.status}


@frappe.whitelist()
def list_sales(profile_id, limit=50, offset=0):
    from book_marketplace.auth.api.auth import _assert_profile_owner
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.view")
    rows = frappe.get_all(
        "InternalOrder",
        filters={"seller_profile": profile_id, "order_type": "Sale"},
        fields=["name", "customer_name", "customer_phone", "status", "payment_status", "total_amount", "order_date", "creation"],
        order_by="creation desc", limit_page_length=int(limit), limit_start=int(offset),
    )
    return rows
