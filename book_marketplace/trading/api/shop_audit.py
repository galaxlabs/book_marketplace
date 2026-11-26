"""Stock audit by publisher: the shop checks its books one publisher at a time and enters the counted quantity.
The counted quantity replaces the system quantity through the same rule as the catalog quantity."""
import json

import frappe
from frappe import _
from frappe.utils import flt


def _owner(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)


def _system_qty(profile_id, book):
    rows = frappe.get_all("StockLedger", filters={"business_profile": profile_id, "book": book, "state": "Available"},
                          pluck="quantity")
    return sum(flt(q) for q in rows)


@frappe.whitelist()
def audit_groups(profile_id):
    """The shop's active books grouped by publisher: [{publisher, name, count}]. Own books have no publisher."""
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "stock.view")
    rows = frappe.db.sql("""
        select b.publisher as publisher, count(*) as cnt
        from `tabBusiness Catalog Item` bci join `tabBook` b on b.name = bci.book
        where bci.business_profile = %s and bci.is_active = 1
        group by b.publisher
    """, (profile_id,), as_dict=True)
    out = []
    for r in rows:
        if not r.publisher or r.publisher == profile_id:
            name = _("Own books / اپنی کتابیں")
            key = ""
        else:
            name = frappe.db.get_value("BusinessProfile", r.publisher, "business_name") or r.publisher
            key = r.publisher
        out.append({"publisher": key, "name": name, "count": r.cnt})
    out.sort(key=lambda g: g["name"])
    return out


@frappe.whitelist()
def audit_books(profile_id, publisher=""):
    """Books of one publisher group in this shop's catalog, with the system quantity to be checked."""
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "stock.view")
    rows = frappe.db.sql("""
        select bci.book as book, b.title as title, b.publisher as publisher
        from `tabBusiness Catalog Item` bci join `tabBook` b on b.name = bci.book
        where bci.business_profile = %s and bci.is_active = 1
        order by b.title
    """, (profile_id,), as_dict=True)
    out = []
    for r in rows:
        own = not r.publisher or r.publisher == profile_id
        if (publisher or "") == "" and not own:
            continue
        if publisher and r.publisher != publisher:
            continue
        out.append({"book": r.book, "title": r.title, "system_qty": _system_qty(profile_id, r.book)})
    return out


@frappe.whitelist()
def save_audit(profile_id, counts):
    """counts: JSON list of {book, counted}. Each counted quantity becomes the shop's stock for that book.
    Returns the changes so the shop and the publisher can see what moved."""
    from book_marketplace.stock_management.api import stock as stock_api
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "stock.edit")
    rows = json.loads(counts) if isinstance(counts, str) else (counts or [])
    changes = []
    for r in rows:
        book = r.get("book")
        if not book or r.get("counted") in (None, ""):
            continue
        counted = int(r["counted"])
        if counted < 0:
            frappe.throw(_("Counted quantity cannot be negative."))
        before = _system_qty(profile_id, book)
        if abs(before - counted) < 0.0001:
            continue
        stock_api.set_catalog_quantity(profile_id, book, counted)
        changes.append({"book": book, "title": frappe.db.get_value("Book", book, "title"),
                        "before": before, "after": counted, "difference": counted - before})
    return {"changed": len(changes), "changes": changes}
