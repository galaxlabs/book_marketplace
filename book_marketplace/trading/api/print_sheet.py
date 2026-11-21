"""Print data for a purchase order: both parties' letterhead, the requested and sent quantities, and transport.
Only the buyer or the seller (logged in as the owner of that profile) can open it. Read only."""
import frappe
from frappe import _
from frappe.utils import flt

from book_marketplace.auth.api.auth import _assert_profile_owner
from book_marketplace.trading.api.invoice import _party


@frappe.whitelist()
def purchase_order_print(name, profile_id):
    _assert_profile_owner(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id not in (doc.seller_profile, doc.buyer_profile):
        frappe.throw(_("Only the buyer or the seller can print this order."), frappe.PermissionError)
    lines = []
    for i in doc.items:
        meta = frappe.db.get_value("Book", i.book, ["title", "author"], as_dict=True) or {}
        lines.append({
            "book": i.book,
            "title": meta.get("title") or i.book,
            "author": meta.get("author") or "",
            "requested_qty": int(i.qty or 0),
            "sent_qty": int(i.sent_qty or 0),
            "unit_price": flt(i.unit_price),
            "option_label": i.get("option_label") or "",
            "item_option": i.get("item_option") or "",
        })
    return {
        "name": doc.name,
        "status": doc.status,
        "role": "seller" if profile_id == doc.seller_profile else "buyer",
        "created": str(doc.creation or "")[:16],
        "dispatched_on": str(doc.dispatched_on or "")[:16],
        "seller": _party(doc.seller_profile),
        "buyer": _party(doc.buyer_profile),
        "transport": {
            "transporter": doc.transporter or "",
            "bilty_no": doc.bilty_no or "",
            "rider_name": doc.rider_name or "",
            "rider_phone": doc.rider_phone or "",
        },
        "packing": {"same_city": bool(doc.get("same_city")), "cartons": int(doc.get("cartons") or 0),
                    "packets": int(doc.get("packets") or 0), "bundles": int(doc.get("bundles") or 0),
                    "tape_status": doc.get("tape_status") or "", "packing_charge": flt(doc.get("packing_charge")),
                    "labour_charge": flt(doc.get("labour_charge")), "station_cost": flt(doc.get("station_cost")),
                    "paid_by": doc.get("packing_paid_by") or ""},
        "freight": {"total": flt(doc.freight_total), "buyer_share": flt(doc.freight_buyer_share),
                    "seller_share": flt(doc.freight_seller_share)},
        "goods_total": sum(l["sent_qty"] * l["unit_price"] for l in lines),
        "invoice_no": doc.invoice_no or "",
        "buyer_doc_no": doc.get("buyer_doc_no") or "", "seller_doc_no": doc.get("seller_doc_no") or "",
        "lines": lines,
        "total_requested": sum(l["requested_qty"] for l in lines),
        "total_sent": sum(l["sent_qty"] for l in lines),
    }
