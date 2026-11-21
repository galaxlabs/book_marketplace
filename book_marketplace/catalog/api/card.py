"""Visiting card design per shop: colours, font, text alignment per part, and which parts show.
Stored as JSON on the shop's store record. The card itself is drawn in the browser."""
import json

import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner

DEFAULT = {
    "bg_from": "#0f766e", "bg_to": "#134e4a", "panel": "#ffffff", "text": "#134e4a", "accent": "#0f766e",
    "font": "sans", "qr_side": "right",
    "align": {"name": "left", "tagline": "left", "details": "left", "address": "left"},
    "show": {"description": True, "tagline": True, "address": True, "phone": True, "email": True, "url": True},
}
FONTS = ("sans", "serif", "mono")
ALIGN = ("left", "center", "right")
COLOR_KEYS = ("bg_from", "bg_to", "panel", "text", "accent")


def _clean(design):
    out = json.loads(json.dumps(DEFAULT))
    if not isinstance(design, dict):
        return out
    for k in COLOR_KEYS:
        v = str(design.get(k) or "")
        if len(v) == 7 and v.startswith("#"):
            out[k] = v
    if design.get("font") in FONTS:
        out["font"] = design["font"]
    if design.get("qr_side") in ("left", "right"):
        out["qr_side"] = design["qr_side"]
    for part, value in (design.get("align") or {}).items():
        if part in out["align"] and value in ALIGN:
            out["align"][part] = value
    for part, value in (design.get("show") or {}).items():
        if part in out["show"]:
            out["show"][part] = bool(value)
    return out


@frappe.whitelist()
def get_card_design(profile_id):
    _assert_profile_owner(profile_id)
    raw = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "card_design")
    try:
        saved = json.loads(raw) if raw else None
    except Exception:
        saved = None
    return _clean(saved)


@frappe.whitelist()
def save_card_design(profile_id, design):
    _assert_profile_owner(profile_id)
    store = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "name")
    if not store:
        frappe.throw(_("Create your store page first, then design the card."))
    clean = _clean(frappe.parse_json(design) if isinstance(design, str) else design)
    frappe.db.set_value("BusinessStore", store, "card_design", json.dumps(clean), update_modified=False)
    return clean
