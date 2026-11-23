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
        # A key of the account itself (User table), sent without the device header, would skip the
        # per-device sign-out and the 30-day limit. Only a System Manager (scripts, integrations) may use one.
        user = frappe.session.user
        if _key_of_request() and user not in ("", "Guest", "Administrator") and "System Manager" not in frappe.get_roles(user):
            raise frappe.AuthenticationError
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


# One-time sign-in codes: the public store hands a signed-in seller to the business panel (another site)
# with a short code in the link, not the key itself. The code works once, for one minute; the panel trades it
# for a key of its own (a new Login Device), so the store's key never appears in a link, history or log.
HANDOFF_SECONDS = 60


def _handoff_key(code):
    return f"bm_handoff:{code}"


@frappe.whitelist()
def make_handoff_code():
    user = frappe.session.user
    if not user or user == "Guest":
        raise frappe.AuthenticationError
    code = frappe.generate_hash(length=32)
    frappe.cache().set_value(_handoff_key(code), user, expires_in_sec=HANDOFF_SECONDS)
    return {"code": code, "expires_in": HANDOFF_SECONDS}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def use_handoff_code(code, device_name=None):
    from book_marketplace.auth.api.auth import new_device_session

    code = str(code or "").strip()
    if len(code) != 32:
        frappe.throw(_("This sign-in link is not valid. Sign in again."), frappe.AuthenticationError)
    key = _handoff_key(code)
    user = frappe.cache().get_value(key)
    frappe.cache().delete_value(key)  # works once, used or not
    if not user or not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(_("This sign-in link has expired. Sign in again."), frappe.AuthenticationError)
    return new_device_session(user, device_name or "Business panel")
