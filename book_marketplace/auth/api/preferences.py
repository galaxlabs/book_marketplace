"""A signed-in account keeps its own look (theme and colors) and language. Other accounts never see them."""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

THEMES = ("white", "indigo", "ink-gold", "custom")


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


@frappe.whitelist()
def my_preferences():
    user = _me()
    row = frappe.db.get_value("Account Preference", user, ["language", "theme_name", "colors"], as_dict=True)
    if not row:
        return {"language": "ur", "theme_name": "white", "colors": {}}
    try:
        colors = json.loads(row.colors or "{}")
    except ValueError:
        colors = {}
    return {"language": row.language or "ur", "theme_name": row.theme_name or "white", "colors": colors}


@frappe.whitelist()
def save_preferences(language="ur", theme_name="white", colors=None):
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
    doc.save(ignore_permissions=True)
    return {"language": language, "theme_name": theme_name, "colors": clean, "saved_on": str(now_datetime())}
