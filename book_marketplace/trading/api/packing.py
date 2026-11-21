"""Packing rules for orders sent to another city.

- Same city (and counter or walk-in sales): no packing question, no packing charge.
- Another city: fewer than `packet_max_books` books go as packets / parcels (about `books_per_packet` each);
  more go in cartons of about `carton_books`. Books are counted as `page_basis`-page books, so thick books fill more.
- A carton must be taped. Taped by us: tape and packing labour per carton. Already taped: load / unload labour per carton.
- Shop to transport station is always the sender's cost.
"""
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from book_marketplace.auth.api.auth import _assert_profile_owner

DEFAULTS = {"packet_max_books": 25, "books_per_packet": 10, "carton_books": 100, "page_basis": 200,
            "tape_charge": 0.0, "packing_labour": 0.0, "packet_charge": 0.0, "load_labour": 0.0,
            "station_cost": 0.0, "charge_packing_to_buyer": 1}
FIELDS = tuple(DEFAULTS)


def settings_of(profile_id):
    row = frappe.db.get_value("Packing Setting", profile_id, FIELDS, as_dict=True) if profile_id else None
    out = dict(DEFAULTS)
    if row:
        for k in FIELDS:
            if row.get(k) not in (None, ""):
                out[k] = row[k]
    for k in ("packet_max_books", "books_per_packet", "carton_books", "page_basis", "charge_packing_to_buyer"):
        out[k] = cint(out[k])
    for k in ("tape_charge", "packing_labour", "packet_charge", "load_labour", "station_cost"):
        out[k] = flt(out[k])
    return out


@frappe.whitelist()
def get_packing_settings(profile_id):
    _assert_profile_owner(profile_id)
    return settings_of(profile_id)


@frappe.whitelist()
def save_packing_settings(profile_id, **values):
    _assert_profile_owner(profile_id)
    data = {}
    for k in ("packet_max_books", "books_per_packet", "carton_books", "page_basis"):
        if k in values:
            v = cint(values[k])
            if v < 1:
                frappe.throw(_("{0} must be 1 or more.").format(k.replace("_", " ")))
            data[k] = v
    for k in ("tape_charge", "packing_labour", "packet_charge", "load_labour", "station_cost"):
        if k in values:
            v = flt(values[k])
            if v < 0:
                frappe.throw(_("Charges cannot be negative."))
            data[k] = v
    if "charge_packing_to_buyer" in values:
        data["charge_packing_to_buyer"] = 1 if str(values["charge_packing_to_buyer"]) in ("1", "true", "True") else 0
    if frappe.db.exists("Packing Setting", profile_id):
        doc = frappe.get_doc("Packing Setting", profile_id)
        doc.update(data)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "Packing Setting", "business_profile": profile_id, **data}).insert(ignore_permissions=True)
    return settings_of(profile_id)


def _norm(city):
    return " ".join((city or "").split()).strip().lower()


def is_same_city(seller, buyer):
    a = _norm(frappe.db.get_value("BusinessProfile", seller, "city"))
    b = _norm(frappe.db.get_value("BusinessProfile", buyer, "city"))
    return bool(a) and a == b


def book_units(lines, page_basis):
    """lines: [(book, copies)]. Copies counted as page_basis-page books (unknown page count = one book)."""
    units = 0.0
    for book, copies in lines:
        pages = cint(frappe.db.get_value("Book", book, "page_count")) if book else 0
        units += copies * (max(pages / page_basis, 0.25) if pages else 1)
    return units


def suggest(settings, copies, units, same_city):
    if same_city or copies <= 0:
        return {"same_city": bool(same_city), "cartons": 0, "packets": 0, "mode": "none"}
    if units < settings["packet_max_books"]:
        return {"same_city": False, "cartons": 0, "packets": max(1, math.ceil(units / settings["books_per_packet"])),
                "mode": "packets"}
    return {"same_city": False, "cartons": max(1, math.ceil(units / settings["carton_books"])), "packets": 0, "mode": "cartons"}


def charges(settings, cartons, packets, tape_status, same_city):
    """Tape and packets, labour and the station cost for what was packed. Nothing for a same-city order."""
    if same_city:
        return {"packing_charge": 0.0, "labour_charge": 0.0, "station_cost": 0.0, "packing_paid_by": ""}
    cartons, packets = max(cint(cartons), 0), max(cint(packets), 0)
    if cartons and tape_status not in ("Already taped", "Taped by us"):
        frappe.throw(_("Say whether the cartons are already taped or taped by you."))
    tape = cartons * settings["tape_charge"] if tape_status == "Taped by us" else 0.0
    labour = cartons * (settings["packing_labour"] if tape_status == "Taped by us" else settings["load_labour"])
    return {"packing_charge": round(tape + packets * settings["packet_charge"], 2), "labour_charge": round(labour, 2),
            "station_cost": round(settings["station_cost"], 2) if (cartons or packets) else 0.0,
            "packing_paid_by": "Buyer" if settings["charge_packing_to_buyer"] else "Seller"}


@frappe.whitelist()
def suggest_packing(name, profile_id, items=None):
    """What to pack for an accepted order: packets or cartons, and the charges. items: optional JSON [{book, sent_qty}]."""
    import json
    _assert_profile_owner(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller packs this order."), frappe.PermissionError)
    sent = {}
    if items:
        for r in (json.loads(items) if isinstance(items, str) else items):
            sent[r.get("book")] = cint(r.get("sent_qty"))
    lines = [(i.book, sent.get(i.book, cint(i.qty))) for i in doc.items]
    s = settings_of(profile_id)
    same = is_same_city(doc.seller_profile, doc.buyer_profile)
    copies = sum(c for _b, c in lines)
    units = book_units(lines, s["page_basis"])
    out = suggest(s, copies, units, same)
    out.update({"copies": copies, "units": round(units, 1), "settings": s})
    out["charges_if_taped_by_us"] = charges(s, out["cartons"], out["packets"], "Taped by us", same) if out["cartons"] else charges(s, 0, out["packets"], "", same)
    out["charges_if_already_taped"] = charges(s, out["cartons"], out["packets"], "Already taped", same) if out["cartons"] else out["charges_if_taped_by_us"]
    return out
