"""Shop notifications: a short note in the shop's panel (for example, an order expired or was disputed)."""
import frappe
from frappe import _
from frappe.utils import now_datetime

from book_marketplace.auth.api.auth import _assert_profile_owner


def notify(profile_id, title, body="", link="", kind="order"):
    """Adds a notification for one shop. Skips empty profiles."""
    if not profile_id:
        return None
    return frappe.get_doc({"doctype": "Shop Notification", "business_profile": profile_id, "kind": kind,
                           "title": title[:140], "body": (body or "")[:500], "link": link or None,
                           "is_read": 0, "created_on": now_datetime()}).insert(ignore_permissions=True).name


@frappe.whitelist()
def my_notifications(profile_id, limit=30):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.view")
    rows = frappe.get_all("Shop Notification", filters={"business_profile": profile_id},
                          fields=["name", "kind", "title", "body", "link", "is_read", "created_on"],
                          order_by="created_on desc", limit_page_length=frappe.utils.cint(limit) or 30)
    for r in rows:
        r["created_on"] = str(r.created_on or "")
    unread = frappe.db.count("Shop Notification", {"business_profile": profile_id, "is_read": 0})
    return {"items": rows, "unread": unread}


@frappe.whitelist()
def mark_notifications_read(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.view")
    frappe.db.sql("update `tabShop Notification` set is_read = 1 where business_profile = %s and is_read = 0", (profile_id,))
    return {"ok": True}
