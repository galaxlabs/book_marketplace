"""Invoices. One invoice per agreed receipt (status Received or Short). The seller issues it with its invoice prefix;
the buyer sees the same invoice under purchases. Only the two parties can open it. Nothing here changes stock or khata."""
import frappe
from frappe import _
from frappe.utils import flt

from book_marketplace.trading.api.purchase_order import _profile_of_user

ISSUED = ("Received", "Short")


def _party(profile_id):
    row = frappe.db.get_value(
        "BusinessProfile", profile_id,
        ["business_name", "address", "city", "phone", "email", "enable_fbr", "fbr_ntn", "fbr_registered_name", "logo"],
        as_dict=True,
    ) or {}
    return {
        "profile": profile_id,
        "name": row.get("business_name") or profile_id,
        "address": row.get("address") or "",
        "city": row.get("city") or "",
        "phone": row.get("phone") or "",
        "email": row.get("email") or "",
        # the tax number is printed whenever the business has written it in its profile
        "ntn": row.get("fbr_ntn") or "",
        "logo": row.get("logo") or "",
    }


@frappe.whitelist()
def list_invoices(profile_id, direction="sales"):
    """direction: 'sales' (this profile issued them as seller) or 'purchase' (this profile received them as buyer)."""
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.view")
    field = "seller_profile" if direction == "sales" else "buyer_profile"
    other = "buyer_profile" if direction == "sales" else "seller_profile"
    rows = frappe.get_all(
        "Purchase Order Request",
        filters={field: profile_id, "status": ["in", list(ISSUED)]},
        fields=["name", "invoice_no", "agreed_on", "status", "invoice_total", other, "total_qty"],
        order_by="agreed_on desc, creation desc",
        limit_page_length=0,
    )
    out = []
    for r in rows:
        out.append({
            "name": r.name,
            "invoice_no": r.invoice_no or "",
            "date": str(r.agreed_on or ""),
            "status": r.status,
            "short": r.status == "Short",
            "counterpart": frappe.db.get_value("BusinessProfile", r[other], "business_name") or r[other],
            "total_qty": int(r.total_qty or 0),
            "total": flt(r.invoice_total),
        })
    return out


@frappe.whitelist()
def design_of(profile_id):
    from book_marketplace.trading.api.design import design_of as _d
    return _d(profile_id)


@frappe.whitelist()
def get_invoice(name, profile_id):
    """The full invoice for printing (letterhead of the seller, bill to the buyer). Only the two parties."""
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "sales.view")
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id not in (doc.seller_profile, doc.buyer_profile):
        frappe.throw(_("Only the buyer or the seller can open this invoice."), frappe.PermissionError)
    if doc.status not in ISSUED or not doc.invoice_no:
        frappe.throw(_("This invoice is not issued yet (order is {0}).").format(doc.status))
    lines = []
    for i in doc.items:
        good = int(i.received_qty or 0) - int(i.damaged_qty or 0)
        lines.append({
            "book": i.book,
            "title": frappe.db.get_value("Book", i.book, "title") or i.book,
            "author": frappe.db.get_value("Book", i.book, "author") or "",
            "sent_qty": int(i.sent_qty or 0),
            "received_qty": int(i.received_qty or 0),
            "damaged_qty": int(i.damaged_qty or 0),
            "billed_qty": good,
            "unit_price": flt(i.unit_price),
            "line_total": flt(i.unit_price) * good,
        })
    subtotal = sum(l["line_total"] for l in lines)
    return {
        "name": doc.name,
        "invoice_no": doc.invoice_no,
        "status": doc.status,
        "date": str(doc.agreed_on or ""),
        "role": "seller" if profile_id == doc.seller_profile else "buyer",
        "seller": _party(doc.seller_profile),
        "buyer": _party(doc.buyer_profile),
        "lines": lines,
        "billed_qty": sum(l["billed_qty"] for l in lines),
        "short_qty": sum(max(l["sent_qty"] - l["received_qty"], 0) for l in lines),
        "damaged_qty": sum(l["damaged_qty"] for l in lines),
        "subtotal": round(subtotal, 2),
        "total": round(flt(doc.invoice_total), 2),
        "freight": {"scenario": doc.freight_scenario or "", "transporter": doc.transporter or "", "buyer_share": flt(doc.freight_buyer_share), "seller_share": flt(doc.freight_seller_share), "payer_is_buyer": flt(doc.freight_buyer_share) > 0},
        "note": doc.receipt_note or "",
        # the seller's saved print design (letterhead and bill terms)
        "design": design_of(doc.seller_profile),
    }
