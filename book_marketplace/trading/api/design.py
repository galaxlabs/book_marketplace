"""Design Studio: how a business's letterhead and bill look on paper.

Saved per business (BusinessProfile.print_design, JSON). Every print reads it: purchase orders, bills, packing
slips and website order bills. Only the owner changes it; the print pages get it with the document.
"""
import json
import re

import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner

FONTS = ("sans", "serif", "mono")
LAYOUTS = ("classic", "centered", "band")
LOGO_SIZES = ("small", "medium", "large")
LOGO_POS = ("left", "right", "top", "none")

LETTERHEAD = {
    "layout": "classic", "accent": "#0f766e", "font": "sans",
    "logo": "left", "logo_size": "medium",
    "name_text": "", "tagline": "",
    "show_city": True, "show_address": True, "show_phone": True, "show_email": True, "show_ntn": True,
    "top_line": True, "bottom_line": True, "footer_text": "",
}
BILL = {
    "accent": "#0f766e", "font": "sans",
    "header_note": "", "show_rate": True, "show_amount_column": True,
    "terms": "", "show_bank": False, "bank_text": "",
    "signature_label": "دستخط / Signature", "show_stamp_box": True, "footer_text": "",
}
DEFAULT = {"letterhead": LETTERHEAD, "bill": BILL}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean_section(raw, base):
    out = dict(base)
    if not isinstance(raw, dict):
        return out
    for k, default in base.items():
        if k not in raw:
            continue
        v = raw[k]
        if isinstance(default, bool):
            out[k] = bool(v)
        elif k == "accent":
            out[k] = v if isinstance(v, str) and HEX.match(v) else default
        elif k == "font":
            out[k] = v if v in FONTS else default
        elif k == "layout":
            out[k] = v if v in LAYOUTS else default
        elif k == "logo_size":
            out[k] = v if v in LOGO_SIZES else default
        elif k == "logo":
            out[k] = v if v in LOGO_POS else default
        elif k in ("terms", "bank_text", "footer_text"):
            out[k] = str(v or "").strip()[:1000]
        else:
            out[k] = str(v or "").strip()[:140]
    return out


def clean(design):
    design = design if isinstance(design, dict) else {}
    return {"letterhead": _clean_section(design.get("letterhead"), LETTERHEAD),
            "bill": _clean_section(design.get("bill"), BILL)}


def design_of(profile_id):
    """The saved design of a business (defaults when it has none). No owner check: prints show it to both parties."""
    raw = frappe.db.get_value("BusinessProfile", profile_id, "print_design") if profile_id else None
    try:
        saved = json.loads(raw) if raw else None
    except ValueError:
        saved = None
    return clean(saved)


@frappe.whitelist()
def get_design(profile_id):
    _assert_profile_owner(profile_id)
    return design_of(profile_id)


@frappe.whitelist()
def save_design(profile_id, design):
    _assert_profile_owner(profile_id)
    data = clean(frappe.parse_json(design) if isinstance(design, str) else design)
    frappe.db.set_value("BusinessProfile", profile_id, "print_design", json.dumps(data), update_modified=False)
    return data


@frappe.whitelist()
def presets():
    """Ready designs to start from. The owner changes any part afterwards."""
    return {
        "classic": {"letterhead": {**LETTERHEAD, "layout": "classic", "accent": "#0f766e"}, "bill": {**BILL, "accent": "#0f766e"}},
        "centered": {"letterhead": {**LETTERHEAD, "layout": "centered", "logo": "top", "accent": "#7c3aed", "font": "serif"},
                     "bill": {**BILL, "accent": "#7c3aed", "font": "serif"}},
        "band": {"letterhead": {**LETTERHEAD, "layout": "band", "logo": "left", "accent": "#1e3a8a"},
                 "bill": {**BILL, "accent": "#1e3a8a", "show_stamp_box": True}},
    }
