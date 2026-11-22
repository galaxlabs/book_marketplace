"""Panel words kept in the database: one row per English text, one column per language.

The panel reads them at start-up and uses them over its built-in dictionary; a System Manager edits them.
"""

import frappe
from frappe import _

LANGS = ["en", "ar", "ur", "hi", "bn", "tr", "ru"]


@frappe.whitelist(allow_guest=True)
def get_ui_translations():
    rows = frappe.get_all(
        "UI Translation",
        fields=["translation_key"] + LANGS,
        limit_page_length=0,
        ignore_permissions=True,
    )
    return [{"key": r.translation_key, **{l: r.get(l) or "" for l in LANGS}} for r in rows]


@frappe.whitelist()
def save_ui_translation(key, values=None):
    """Save the words of one key. `values` is a dict like {"ur": "...", "hi": "..."}; empty text clears that language."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only an administrator can change the words."), frappe.PermissionError)
    key = (key or "").strip()
    if not key:
        frappe.throw(_("Write the English text (the key)."))
    if isinstance(values, str):
        values = frappe.parse_json(values)
    values = values or {}
    bad = [l for l in values if l not in LANGS]
    if bad:
        frappe.throw(_("Unknown language: {0}").format(", ".join(bad)))
    name = frappe.db.get_value("UI Translation", {"translation_key": key}, "name")
    if name:
        doc = frappe.get_doc("UI Translation", name)
    else:
        doc = frappe.new_doc("UI Translation")
        doc.translation_key = key
    for l, text in values.items():
        doc.set(l, (text or "").strip())
    doc.save(ignore_permissions=True)
    return {"key": key, **{l: doc.get(l) or "" for l in LANGS}}
