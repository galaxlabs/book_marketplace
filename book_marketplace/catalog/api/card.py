"""Visiting card design per shop: colours, font, text alignment per part, and which parts show.
Stored as JSON on the shop's store record. The card itself is drawn in the browser."""
import json

import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner

DEFAULT = {
    "bg_from": "#065f46", "bg_to": "#022c22", "panel": "#ffffff", "text": "#022c22", "accent": "#065f46",
    "font": "sans", "qr_side": "right",
    "align": {"name": "left", "tagline": "left", "details": "left", "address": "left"},
    "show": {"description": True, "tagline": True, "address": True, "phone": True, "email": True, "url": True},
    "qr": {
        "dark": "#022c22", "light": "#ffffff", "frame": "#065f46", "frame_width": 14, "radius": 28,
        "center": "none", "center_text": "", "logo": "", "logo_size": 22, "caption": "اسکین کریں",
    },
}
FONTS = ("sans", "serif", "mono")
ALIGN = ("left", "center", "right")
COLOR_KEYS = ("bg_from", "bg_to", "panel", "text", "accent")
QR_COLORS = ("dark", "light", "frame")
QR_CENTER = ("none", "text", "logo")
LOGO_MAX = 300_000  # characters of the small PNG kept as a data link


def _color(v):
    v = str(v or "")
    return v if len(v) == 7 and v.startswith("#") else None


def _number(v, low, high):
    try:
        return max(low, min(high, int(v)))
    except (TypeError, ValueError):
        return None


def _clean_qr(qr, out):
    if not isinstance(qr, dict):
        return
    for k in QR_COLORS:
        if _color(qr.get(k)):
            out[k] = qr[k]
    for k, low, high in (("frame_width", 0, 40), ("radius", 0, 60), ("logo_size", 14, 28)):
        n = _number(qr.get(k), low, high)
        if n is not None:
            out[k] = n
    if qr.get("center") in QR_CENTER:
        out["center"] = qr["center"]
    out["center_text"] = str(qr.get("center_text") or "")[:24]
    out["caption"] = str(qr.get("caption") if qr.get("caption") is not None else out["caption"])[:40]
    logo = str(qr.get("logo") or "")
    out["logo"] = logo if logo.startswith("data:image/png;base64,") and len(logo) <= LOGO_MAX else ""


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
    _clean_qr(design.get("qr"), out["qr"])
    return out


@frappe.whitelist()
def get_card_design(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.view")
    raw = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "card_design")
    try:
        saved = json.loads(raw) if raw else None
    except Exception:
        saved = None
    return _clean(saved)


@frappe.whitelist()
def save_card_design(profile_id, design):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "settings.manage")
    store = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "name")
    if not store:
        frappe.throw(_("Create your store page first, then design the card."))
    clean = _clean(frappe.parse_json(design) if isinstance(design, str) else design)
    frappe.db.set_value("BusinessStore", store, "card_design", json.dumps(clean), update_modified=False)
    return clean
