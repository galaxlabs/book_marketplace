"""An account may hold one profile per business type. It can switch a type on (approved at once) or off (Inactive)."""
import frappe
from frappe import _

from book_marketplace.auth.api.auth import PROFILE_TYPES, is_profile_type_enabled


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


@frappe.whitelist()
def my_profile_types():
    user = _me()
    rows = frappe.get_all("BusinessProfile", filters={"user": user},
                          fields=["name", "profile_type", "business_name", "status"], order_by="creation asc")
    have = {r.profile_type: r for r in rows}
    types = []
    for t in PROFILE_TYPES:
        r = have.get(t)
        types.append({"profile_type": t, "enabled_in_marketplace": bool(is_profile_type_enabled(t)),
                      "profile": r.name if r else None, "status": r.status if r else None,
                      "business_name": r.business_name if r else None})
    return {"profiles": rows, "types": types}


@frappe.whitelist()
def set_profile_type(profile_type, active=1, business_name=""):
    user = _me()
    if profile_type not in PROFILE_TYPES:
        frappe.throw(_("Invalid business type."))
    on = str(active).lower() in ("1", "true", "yes")
    existing = frappe.db.get_value("BusinessProfile", {"user": user, "profile_type": profile_type},
                                   ["name", "business_name", "status"], as_dict=True)
    if on:
        if not is_profile_type_enabled(profile_type):
            frappe.throw(_("This business type is not enabled in Marketplace Settings."))
        if existing:
            if existing.status != "Approved":
                frappe.db.set_value("BusinessProfile", existing.name, "status", "Approved")
            name = existing.name
        else:
            base = frappe.db.get_value("BusinessProfile", {"user": user}, "business_name") or \
                frappe.db.get_value("User", user, "full_name") or user
            name = frappe.get_doc({"doctype": "BusinessProfile", "user": user, "profile_type": profile_type,
                                   "business_name": (business_name or base).strip()[:140],
                                   "status": "Approved"}).insert(ignore_permissions=True).name
    else:
        if not existing:
            frappe.throw(_("You do not have this business type."))
        frappe.db.set_value("BusinessProfile", existing.name, "status", "Inactive")
        name = existing.name
    return {"profile": name, "profile_type": profile_type, "status": frappe.db.get_value("BusinessProfile", name, "status")}
