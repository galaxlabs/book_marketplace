"""Sign-in per device. Each login makes its own key and secret (Login Device). The request names the
"Login Device" source, so Frappe checks this table and not the one secret of the account.

- A device signed out (its own Log out) gets 401 at once.
- A device not used for 30 days is signed out by itself (checked on its next request).
- last_used is written at most once a day, so ordinary requests do not write.
"""
import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime

SOURCE = "Login Device"
IDLE_DAYS = 30


def _key_of_request():
    parts = (frappe.get_request_header("Authorization", "") or "").split(" ")
    if len(parts) != 2 or parts[0].lower() != "token" or ":" not in parts[1]:
        return ""
    return parts[1].split(":", 1)[0]


def check():
    """Called by Frappe after the key is checked (auth_hooks)."""
    if (frappe.get_request_header("Frappe-Authorization-Source") or "") != SOURCE:
        return
    if not frappe.session.user or frappe.session.user == "Guest":
        return
    key = _key_of_request()
    row = frappe.db.get_value("Login Device", {"api_key": key}, ["name", "last_used", "enabled"], as_dict=True)
    if not row or not row.enabled:
        raise frappe.AuthenticationError
    now = now_datetime()
    last = get_datetime(row.last_used) if row.last_used else now
    if last < add_days(now, -IDLE_DAYS):
        frappe.db.set_value("Login Device", row.name, "enabled", 0)
        frappe.db.commit()
        raise frappe.AuthenticationError
    if last < add_days(now, -1):
        frappe.db.set_value("Login Device", row.name, "last_used", now, update_modified=False)


@frappe.whitelist()
def logout_device():
    """Signs out this device only: its key stops working at once."""
    if (frappe.get_request_header("Frappe-Authorization-Source") or "") != SOURCE:
        frappe.throw(_("Sign in again to log out."))
    key = _key_of_request()
    name = frappe.db.get_value("Login Device", {"api_key": key}, "name")
    if name:
        frappe.db.set_value("Login Device", name, "enabled", 0)
    return {"status": "signed_out"}
