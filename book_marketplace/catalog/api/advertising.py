"""Advertising on the public store: a business marks up to 5 of its listed books as advertised. Those books are
shown first on its public store, with an advertisement label. Free for now (no payment here)."""
import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner

MAX_ADVERTISED = 5


@frappe.whitelist()
def set_advertised(profile_id, item_id, advertised=1):
    _assert_profile_owner(profile_id)
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    if doc.business_profile != profile_id:
        frappe.throw(_("Not permitted for this book."), frappe.PermissionError)
    on = frappe.utils.cint(advertised) == 1
    if on and not doc.is_advertised:
        if not doc.is_active:
            frappe.throw(_("Make the book visible on the store before advertising it."))
        count = frappe.db.count("BusinessCatalogItem",
                                {"business_profile": profile_id, "is_advertised": 1, "is_active": 1})
        if count >= MAX_ADVERTISED:
            frappe.throw(_("A business can advertise at most {0} books. Remove one first.").format(MAX_ADVERTISED))
    doc.is_advertised = 1 if on else 0
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "is_advertised": bool(doc.is_advertised)}
