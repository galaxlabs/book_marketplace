"""Phase 1 purchase orders: a shop orders books from a seller (publisher or shop) that lists them.
The seller accepts or rejects. Quantities of the agreed order come in phase 2 (dispatch) and phase 3 (receipt)."""
import json

import frappe
from frappe import _
from frappe.utils import flt


def _profile_of_user(profile_id):
    """The profile must belong to the logged-in account (admins may act for any profile)."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)


def _listed_price(seller, book):
    """The seller's active price for the book. Only a book the seller lists can be ordered from it."""
    row = frappe.db.get_value("BusinessCatalogItem", {"business_profile": seller, "book": book, "is_active": 1},
                              ["selling_price", "trade_price"], as_dict=True)
    if not row:
        frappe.throw(_("This seller does not list the book {0}.").format(book))
    return flt(row.trade_price or row.selling_price)


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
                   "unit_price": flt(i.unit_price), "line_total": flt(i.line_total)} for i in doc.items],
        "creation": str(doc.creation),
    }


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
