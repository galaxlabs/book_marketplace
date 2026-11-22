"""Advertising on the public site. A business has one advertising option at a time (free for now):
- "Store": its whole store is shown first in the store list, with an advertisement label.
- "Books": up to 5 of its listed books are shown first on its own store, with the label.
Switching the option off "Books" clears the book ads. A price per option can be added later."""
import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner

MAX_ADVERTISED = 5
OPTIONS = ("", "Store", "Books")


@frappe.whitelist()
def get_ad_option(profile_id):
    _assert_profile_owner(profile_id)
    option = frappe.db.get_value("BusinessProfile", profile_id, "ad_option") or ""
    count = frappe.db.count("BusinessCatalogItem", {"business_profile": profile_id, "is_advertised": 1, "is_active": 1})
    return {"option": option, "advertised_books": count, "max_books": MAX_ADVERTISED}


@frappe.whitelist()
def set_ad_option(profile_id, option):
    _assert_profile_owner(profile_id)
    option = option or ""
    if option not in OPTIONS:
        frappe.throw(_("Choose Store, Books, or none."))
    previous = frappe.db.get_value("BusinessProfile", profile_id, "ad_option") or ""
    if previous == "Books" and option != "Books":
        # the book ads go with the Books option
        for name in frappe.get_all("BusinessCatalogItem", filters={"business_profile": profile_id, "is_advertised": 1},
                                   pluck="name"):
            frappe.db.set_value("BusinessCatalogItem", name, "is_advertised", 0)
    frappe.db.set_value("BusinessProfile", profile_id, "ad_option", option or None)
    return get_ad_option(profile_id)


@frappe.whitelist()
def set_advertised(profile_id, item_id, advertised=1):
    _assert_profile_owner(profile_id)
    if (frappe.db.get_value("BusinessProfile", profile_id, "ad_option") or "") != "Books":
        frappe.throw(_("Choose the Books option first to advertise books."))
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
