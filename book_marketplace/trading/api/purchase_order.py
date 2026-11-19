"""Purchase orders. Phase 1: a shop orders books from a seller (publisher or shop) that lists them.
Phase 2: the seller writes the sent quantity per book and dispatches; the order then carries a QR.
The seller accepts or rejects. Phase 3: the buyer receives (received quantities, stock goes into its shop).
Phase 4: the agreed invoice is made from the received quantities; a short delivery is flagged for the seller."""
import json

import frappe
from frappe import _
from frappe.utils import flt


def _profile_of_user(profile_id):
    """The profile must belong to the logged-in account (admins may act for any profile)."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)


def _price_info(seller, book):
    """(mrp, discount_percent, net) for a book from a seller.
    MRP is the printed price (Book.set_mrp). A listed book uses the seller's trade or selling price.
    A publisher's own book not in its catalog uses the publisher's general discount rate on the MRP.
    Any other book must be listed by the seller."""
    mrp = flt(frappe.db.get_value("Book", book, "set_mrp"))
    row = frappe.db.get_value("BusinessCatalogItem", {"business_profile": seller, "book": book, "is_active": 1},
                              ["selling_price", "trade_price", "discount_percent"], as_dict=True)
    if row:
        # a listed book with no price of its own is sold at its MRP
        net = flt(row.trade_price or row.selling_price) or mrp
        disc = flt(row.discount_percent) or (round((1 - net / mrp) * 100, 2) if mrp and net < mrp else 0)
        return mrp, disc, net
    if frappe.db.get_value("Book", book, "publisher") == seller:
        disc = flt(frappe.db.get_value("BusinessProfile", seller, "default_discount_percent"))
        return mrp, disc, round(mrp * (1 - disc / 100), 2)
    frappe.throw(_("This seller does not list the book {0}.").format(book))


def _listed_price(seller, book):
    return _price_info(seller, book)[2]


@frappe.whitelist()
def create_purchase_order(buyer_profile, seller_profile, items, notes=""):
    """items: JSON list of {book, qty}. The price is the seller's listed price (trade price if it has one)."""
    _profile_of_user(buyer_profile)
    if buyer_profile == seller_profile:
        frappe.throw(_("Choose another business for the order."))
    if not frappe.db.exists("BusinessProfile", seller_profile):
        frappe.throw(_("Seller not found."))
    rows = json.loads(items) if isinstance(items, str) else (items or [])
    rows = [r for r in rows if r.get("book") and int(r.get("qty") or 0) > 0]
    if not rows:
        frappe.throw(_("Add at least one book with a quantity."))
    doc = frappe.get_doc({
        "doctype": "Purchase Order Request", "buyer_profile": buyer_profile, "seller_profile": seller_profile,
        "status": "Sent", "notes": (notes or "").strip()[:500],
    })
    for r in rows:
        price = _listed_price(seller_profile, r["book"])
        qty = int(r["qty"])
        doc.append("items", {"book": r["book"], "qty": qty, "unit_price": price, "line_total": price * qty})
    doc.total_qty = sum(int(i.qty) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    doc.insert(ignore_permissions=True)
    return _view(doc)


def _view(doc):
    return {
        "name": doc.name, "status": doc.status, "buyer_profile": doc.buyer_profile,
        "buyer_name": frappe.db.get_value("BusinessProfile", doc.buyer_profile, "business_name"),
        "seller_profile": doc.seller_profile,
        "seller_name": frappe.db.get_value("BusinessProfile", doc.seller_profile, "business_name"),
        "notes": doc.notes or "", "total_qty": doc.total_qty, "total": flt(doc.total),
        "items": [{"book": i.book, "title": frappe.db.get_value("Book", i.book, "title"), "qty": i.qty,
                   "sent_qty": i.sent_qty if i.sent_qty is not None else None,
                   "received_qty": i.received_qty if i.received_qty is not None else None,
                   "unit_price": flt(i.unit_price), "line_total": flt(i.line_total)} for i in doc.items],
        "creation": str(doc.creation),
        "dispatched_on": str(doc.dispatched_on or ""),
        # the QR on the dispatched order holds only its number; the buyer scans it to open the invoice
        "qr": doc.name if doc.status in ("Dispatched", "Received", "Short") else "",
        "received_on": str(doc.received_on or ""),
        "invoice_no": doc.invoice_no or "",
        "invoice_total": flt(doc.invoice_total),
        # a short delivery (received less than sent) is flagged so the seller sees it in its list
        "difference": doc.status == "Short",
        # shortage: books sent but not received; extra: books received above what was sent
        "difference_qty": sum(max(int(i.sent_qty or 0) - int(i.received_qty or 0), 0) for i in doc.items
                              if i.received_qty is not None),
        "extra_qty": sum(max(int(i.received_qty or 0) - int(i.sent_qty or 0), 0) for i in doc.items
                         if i.received_qty is not None),
    }


def _on_hand(profile_id, book):
    """The buyer's current stock of a book (all branches, Available)."""
    if not profile_id:
        return 0
    return sum(flt(q) for q in frappe.get_all("StockLedger", filters={"business_profile": profile_id, "book": book,
                                                                       "state": "Available"}, pluck="quantity"))


@frappe.whitelist()
def seller_catalog(seller_profile, buyer_profile=None):
    """Everything the seller can supply, for the buyer's order form: all books the seller publishes (its list),
    plus books it lists in its active catalog. Each row carries the buyer's on-hand quantity; books the buyer
    already holds come first, so the buyer can order more in the quantity box. Any logged-in account."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    rows = _seller_books(seller_profile)
    for r in rows:
        r["on_hand"] = _on_hand(buyer_profile, r["book"])
    rows.sort(key=lambda x: (x["on_hand"] <= 0, (x["title"] or "").lower()))
    return rows


def _seller_books(seller_profile):
    """Each row: title, author, MRP, the discount rate that applies, the net price, and the estimate basis."""
    listed = {r.book for r in frappe.get_all(
        "BusinessCatalogItem", filters={"business_profile": seller_profile, "is_active": 1},
        fields=["book"], limit_page_length=0)}
    published = {b.name: b for b in frappe.get_all("Book", filters={"publisher": seller_profile},
                                                   fields=["name", "title", "author"], limit_page_length=0)}
    names = set(published) | listed
    out = []
    for name in names:
        b = published.get(name) or frappe.db.get_value("Book", name, ["name", "title", "author"], as_dict=True)
        if not b:
            continue
        mrp, disc, net = _price_info(seller_profile, name)
        out.append({"book": name, "title": b.title, "author": b.author, "mrp": mrp,
                    "discount_percent": disc, "price": net, "discount_amount": round(mrp - net, 2)})
    return sorted(out, key=lambda x: (x["title"] or "").lower())


@frappe.whitelist()
def find_sellers(text=""):
    """Business profiles of any type (publisher, bookseller, distributor, agent) whose name contains the text.
    The order form uses it to find the publisher or shop to order from. Any logged-in account."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    t = (text or "").strip()
    filters = {"business_name": ["like", f"%{t}%"]} if t else {}
    rows = frappe.get_all("BusinessProfile", filters=filters,
                          fields=["name", "business_name", "profile_type", "city"],
                          order_by="business_name asc", limit_page_length=1000)
    return [{"business_profile": r.name, "business_name": r.business_name, "profile_type": r.profile_type,
             "city": r.city or ""} for r in rows]


@frappe.whitelist()
def set_dispatch(name, profile_id, items):
    """The seller prepares the order: writes the quantity it sends for each book, then packs and dispatches.
    items: JSON list of {book, sent_qty}. Only the seller, only while the order is Accepted."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller dispatches this order."), frappe.PermissionError)
    if doc.status != "Accepted":
        frappe.throw(_("Only an accepted order can be dispatched (now: {0}).").format(doc.status))
    sent = {r["book"]: int(r.get("sent_qty") or 0) for r in (json.loads(items) if isinstance(items, str) else (items or []))}
    for row in doc.items:
        if row.book not in sent:
            frappe.throw(_("Enter the sent quantity for {0}.").format(row.book))
        q = sent[row.book]
        if q < 0 or q > int(row.qty):
            frappe.throw(_("Sent quantity for {0} must be between 0 and {1}.").format(row.book, row.qty))
        row.sent_qty = q
        row.line_total = flt(row.unit_price) * q
    doc.total_qty = sum(int(i.sent_qty or 0) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    doc.status = "Dispatched"
    doc.dispatched_on = now_datetime()
    doc.save(ignore_permissions=True)
    return _view(doc)


@frappe.whitelist()
def purchase_orders(profile_id, direction="buy"):
    """direction 'buy': orders this profile placed. 'sell': orders this profile received."""
    _profile_of_user(profile_id)
    field = "buyer_profile" if direction == "buy" else "seller_profile"
    names = frappe.get_all("Purchase Order Request", filters={field: profile_id}, pluck="name",
                           order_by="creation desc", limit_page_length=200)
    return [_view(frappe.get_doc("Purchase Order Request", n)) for n in names]


@frappe.whitelist()
def purchase_order_detail(name):
    doc = frappe.get_doc("Purchase Order Request", name)
    mine = [p for p in (doc.buyer_profile, doc.seller_profile)
            if frappe.db.get_value("BusinessProfile", p, "user") == frappe.session.user]
    if not mine and frappe.session.user != "Administrator":
        frappe.throw(_("This order is not yours."), frappe.PermissionError)
    return _view(doc)


@frappe.whitelist()
def respond_purchase_order(name, profile_id, decision):
    """The seller accepts or rejects a sent order. Only the seller can answer; the buyer can cancel while it is sent."""
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if decision not in ("Accepted", "Rejected", "Cancelled"):
        frappe.throw(_("Unknown decision."))
    if decision == "Cancelled":
        if profile_id != doc.buyer_profile:
            frappe.throw(_("Only the buyer can cancel."), frappe.PermissionError)
    elif profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller can accept or reject."), frappe.PermissionError)
    if doc.status != "Sent":
        frappe.throw(_("This order is already {0}.").format(doc.status))
    doc.status = decision
    doc.save(ignore_permissions=True)
    return _view(doc)


def _add_stock(profile_id, book, qty):
    """Put received books into the buyer's own shop stock (its main branch)."""
    if qty <= 0:
        return
    from book_marketplace.stock_management.api.stock import _default_branch
    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book, "branch_location": branch, "state": "Available"},
        ["name", "quantity"], as_dict=True,
    )
    if existing:
        frappe.db.set_value("StockLedger", existing.name, "quantity", flt(existing.quantity) + qty)
    else:
        frappe.get_doc({
            "doctype": "StockLedger", "book": book, "business_profile": profile_id,
            "branch_location": branch, "quantity": qty, "state": "Available",
        }).insert(ignore_permissions=True)


@frappe.whitelist()
def receive_purchase_order(name, profile_id, items):
    """The buyer receives a dispatched order. items: JSON list of {book, received_qty} (0 or more; can be more than sent).
    The received books go into the buyer's stock, and the agreed invoice is made from what was received.
    Fewer than sent marks the order Short; more than sent is recorded as extra (invoiced at the agreed price)."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.buyer_profile:
        frappe.throw(_("Only the buyer receives this order."), frappe.PermissionError)
    if doc.status != "Dispatched":
        frappe.throw(_("Only a dispatched order can be received (now: {0}).").format(doc.status))
    got = {r["book"]: int(r.get("received_qty") or 0) for r in (json.loads(items) if isinstance(items, str) else (items or []))}
    short = False
    for row in doc.items:
        if row.book not in got:
            frappe.throw(_("Enter the received quantity for {0}.").format(row.book))
        q = got[row.book]
        sent = int(row.sent_qty or 0)
        if q < 0:
            frappe.throw(_("Received quantity for {0} cannot be negative.").format(row.book))
        if q < sent:
            short = True
        row.received_qty = q
        row.line_total = flt(row.unit_price) * q
    for row in doc.items:
        _add_stock(profile_id, row.book, int(row.received_qty or 0))
    doc.total_qty = sum(int(i.received_qty or 0) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    doc.invoice_total = doc.total
    doc.invoice_no = f"INV-{doc.name}"
    doc.received_on = now_datetime()
    doc.status = "Short" if short else "Received"
    doc.save(ignore_permissions=True)
    return _view(doc)

