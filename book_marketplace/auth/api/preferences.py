"""A signed-in account keeps its own look (theme and colors) and language. Other accounts never see them."""
import json

from zoneinfo import available_timezones

import frappe
from frappe import _
from frappe.utils import get_system_timezone, now_datetime

THEMES = ("white", "indigo", "ink-gold", "custom")
# an account that has not picked a zone reads times in Pakistan time
DEFAULT_ZONE = "Asia/Karachi"


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


@frappe.whitelist()
def my_preferences():
    user = _me()
    row = frappe.db.get_value("Account Preference", user, ["language", "theme_name", "colors", "brand_display", "time_zone"], as_dict=True)
    # server_time_zone: the zone the server writes its times in, so the panel can show them in the account's zone
    server_zone = get_system_timezone()
    if not row:
        return {"language": "ur", "theme_name": "white", "colors": {}, "brand_display": "both",
                "time_zone": DEFAULT_ZONE, "server_time_zone": server_zone}
    try:
        colors = json.loads(row.colors or "{}")
    except ValueError:
        colors = {}
    return {"language": row.language or "ur", "theme_name": row.theme_name or "white", "colors": colors,
            "brand_display": row.brand_display or "both", "time_zone": row.time_zone or DEFAULT_ZONE,
            "server_time_zone": server_zone}


@frappe.whitelist()
def save_preferences(language="ur", theme_name="white", colors=None, brand_display=None, time_zone=None):
    user = _me()
    if language not in ("ur", "en"):
        frappe.throw(_("Unknown language."))
    if theme_name not in THEMES:
        frappe.throw(_("Unknown theme."))
    data = json.loads(colors) if isinstance(colors, str) else (colors or {})
    clean = {}
    for key, value in (data or {}).items():
        v = str(value or "").strip()
        if len(key) > 40 or not v.startswith("#") or len(v) not in (4, 7, 9):
            frappe.throw(_("Colors must be hex values like #1f6f6b."))
        clean[key] = v
    if frappe.db.exists("Account Preference", user):
        doc = frappe.get_doc("Account Preference", user)
    else:
        doc = frappe.get_doc({"doctype": "Account Preference", "user": user})
    doc.language = language
    doc.theme_name = theme_name
    doc.colors = json.dumps(clean)
    if brand_display is not None:
        if brand_display not in ("both", "logo", "name"):
            frappe.throw(_("Unknown header display."))
        doc.brand_display = brand_display
    if time_zone is not None:
        # changed only when sent; must be a real zone name such as Asia/Karachi
        if time_zone not in available_timezones():
            frappe.throw(_("Unknown time zone."))
        doc.time_zone = time_zone
    doc.save(ignore_permissions=True)
    return {"language": language, "theme_name": theme_name, "colors": clean, "brand_display": doc.brand_display or "both",
            "time_zone": doc.time_zone or DEFAULT_ZONE, "saved_on": str(now_datetime())}
