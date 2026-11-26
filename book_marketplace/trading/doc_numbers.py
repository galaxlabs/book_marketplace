"""Document numbers each business reads as its own, and its saved buyers and sellers.

Number: PREFIX-DOC-LETTERS-YYMM-NNNN, for example MBL-BILL-ABD-2611-0007.
- PREFIX: the business's own (Document Number Setting), else its invoice prefix, else the initials of its name.
- DOC: what it is (PO, BILL, CO).
- LETTERS: the other party's code (saved party), else the initials of its name; left out when switched off.
- NNNN: a counter per prefix, kind, letters and month, from Frappe's series table (never repeats).
The internal record name (PO-2026-11-21-0001) stays as it is; this number is printed and used as the file name.
"""
import re

import frappe
from frappe import _
from frappe.model.naming import make_autoname

from book_marketplace.auth.api.auth import _assert_profile_owner


def _clean(code, size=8):
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())[:size]


def initials(name, size=4):
    """Latin initials of a name (Ahmad Book Depot: ABD). Urdu-only names give an empty string."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    if not words:
        return ""
    if len(words) == 1:
        return _clean(words[0], 3)
    return _clean("".join(w[0] for w in words), size)


def business_prefix(profile_id):
    row = frappe.db.get_value("Document Number Setting", profile_id, ["prefix", "add_party_letters"], as_dict=True)
    if row and row.prefix:
        return _clean(row.prefix), bool(row.add_party_letters)
    bp = frappe.db.get_value("BusinessProfile", profile_id, ["invoice_prefix", "business_name"], as_dict=True) or {}
    code = _clean(bp.get("invoice_prefix")) or initials(bp.get("business_name")) or _clean(profile_id.split("-")[-1]) or "BB"
    return code, (row.add_party_letters if row else True)


def party_code(owner_profile, party_profile=None, party_name=""):
    """The letters for the other party: its saved code, else initials of its name, else its id digits."""
    if party_profile:
        code = frappe.db.get_value("Business Party", {"business_profile": owner_profile, "linked_profile": party_profile}, "party_code")
        if code:
            return _clean(code, 6)
        party_name = party_name or frappe.db.get_value("BusinessProfile", party_profile, "business_name") or ""
    elif party_name:
        code = frappe.db.get_value("Business Party", {"business_profile": owner_profile, "party_name": party_name}, "party_code")
        if code:
            return _clean(code, 6)
    return initials(party_name) or (_clean(party_profile.split("-")[0][:1] + party_profile.split("-")[-1], 6) if party_profile else "")


def next_number(owner_profile, kind, party_profile=None, party_name=""):
    prefix, with_letters = business_prefix(owner_profile)
    letters = party_code(owner_profile, party_profile, party_name) if with_letters else ""
    head = "-".join(x for x in (prefix, kind, letters) if x)
    made = make_autoname(f"{head}-.YY..MM.-.####")
    return made


def remember_party(owner_profile, party_profile, party_type):
    """Saves the other side of an order in the owner's list, once. Buyer + seller on both sides becomes Both."""
    if not party_profile or owner_profile == party_profile:
        return
    existing = frappe.db.get_value("Business Party", {"business_profile": owner_profile, "linked_profile": party_profile},
                                   ["name", "party_type"], as_dict=True)
    if existing:
        if existing.party_type not in (party_type, "Both"):
            frappe.db.set_value("Business Party", existing.name, "party_type", "Both")
        return
    bp = frappe.db.get_value("BusinessProfile", party_profile, ["business_name", "city", "phone"], as_dict=True) or {}
    frappe.get_doc({"doctype": "Business Party", "business_profile": owner_profile, "linked_profile": party_profile,
                    "party_name": bp.get("business_name") or party_profile, "party_type": party_type,
                    "city": bp.get("city") or "", "phone": bp.get("phone") or ""}).insert(ignore_permissions=True)


# ---------- settings ----------

@frappe.whitelist()
def get_number_settings(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.view")
    prefix, letters = business_prefix(profile_id)
    own = frappe.db.get_value("Document Number Setting", profile_id, "prefix") or ""
    return {"prefix": own, "effective_prefix": prefix, "add_party_letters": 1 if letters else 0,
            "example": "-".join(x for x in (prefix, "BILL", "ABD" if letters else "") if x) + "-" + frappe.utils.nowdate()[2:4] + frappe.utils.nowdate()[5:7] + "-0001"}


@frappe.whitelist()
def save_number_settings(profile_id, prefix="", add_party_letters=1):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "settings.manage")
    p = _clean(prefix)
    if prefix and not (2 <= len(p) <= 8):
        frappe.throw(_("The prefix is 2 to 8 English letters or digits."))
    letters = 1 if str(add_party_letters) in ("1", "true", "True") else 0
    if frappe.db.exists("Document Number Setting", profile_id):
        doc = frappe.get_doc("Document Number Setting", profile_id)
        doc.prefix, doc.add_party_letters = p, letters
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "Document Number Setting", "business_profile": profile_id, "prefix": p,
                        "add_party_letters": letters}).insert(ignore_permissions=True)
    return get_number_settings(profile_id)


# ---------- saved parties ----------

FIELDS = ["name", "party_name", "party_type", "party_code", "linked_profile", "phone", "city", "address"]


@frappe.whitelist()
def my_parties(profile_id, party_type="", text=""):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.view")
    filters = {"business_profile": profile_id}
    if party_type in ("Buyer", "Seller"):
        filters["party_type"] = ["in", [party_type, "Both"]]
    rows = frappe.get_all("Business Party", filters=filters, fields=FIELDS, order_by="party_name asc", limit_page_length=0)
    t = (text or "").strip().lower()
    out = []
    for r in rows:
        if t and t not in (r.party_name or "").lower() and t not in (r.city or "").lower():
            continue
        r["letters"] = _clean(r.party_code, 6) or initials(r.party_name) or ""
        out.append(r)
    return out


@frappe.whitelist()
def save_party(profile_id, party_name, party_type="Buyer", party_code="", phone="", city="", address="", linked_profile=None, name=None):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.create")
    party_name = " ".join((party_name or "").split())
    if not party_name:
        frappe.throw(_("Write the name."))
    if party_type not in ("Buyer", "Seller", "Both"):
        frappe.throw(_("Choose buyer, seller or both."))
    code = _clean(party_code, 6)
    if party_code and len(code) < 2:
        frappe.throw(_("The letters are 2 to 6 English letters or digits."))
    if linked_profile and not frappe.db.exists("BusinessProfile", linked_profile):
        frappe.throw(_("Business not found."))
    data = {"party_name": party_name[:140], "party_type": party_type, "party_code": code, "phone": (phone or "")[:40],
            "city": (city or "")[:80], "address": (address or "")[:300], "linked_profile": linked_profile or None}
    if name:
        doc = frappe.get_doc("Business Party", name)
        if doc.business_profile != profile_id:
            frappe.throw(_("This contact is not yours."), frappe.PermissionError)
        doc.update(data)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "Business Party", "business_profile": profile_id, **data}).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def delete_party(profile_id, name):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.create")
    if frappe.db.get_value("Business Party", name, "business_profile") != profile_id:
        frappe.throw(_("This contact is not yours."), frappe.PermissionError)
    frappe.delete_doc("Business Party", name, ignore_permissions=True)
    return {"status": "deleted"}
