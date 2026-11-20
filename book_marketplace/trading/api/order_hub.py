"""One list for the Orders page: purchase orders (as buyer or seller) and website orders that customers placed
with this shop, newest first. Read only. Only the shop's own orders; each row says which kind it is."""
import frappe

from book_marketplace.auth.api.auth import _assert_profile_owner

PO_FIELDS = ["name", "status", "buyer_profile", "seller_profile", "creation", "invoice_no", "invoice_total",
             "bilty_no", "dispatched_on"]


@frappe.whitelist()
def order_hub(profile_id, kind="all", status=None, limit=100):
    _assert_profile_owner(profile_id)
    limit = min(frappe.utils.cint(limit) or 100, 300)
    rows = []
    if kind in ("all", "purchase"):
        for role, key in (("seller", "seller_profile"), ("buyer", "buyer_profile")):
            filters = {key: profile_id}
            if status:
                filters["status"] = status
            for po in frappe.get_all("Purchase Order Request", filters=filters, fields=PO_FIELDS,
                                     order_by="creation desc", limit_page_length=limit):
                counterparty_key = "buyer_profile" if role == "seller" else "seller_profile"
                rows.append({
                    "kind": "purchase", "name": po.name, "role": role, "status": po.status,
                    "counterparty": frappe.db.get_value("BusinessProfile", po[counterparty_key], "business_name") or po[counterparty_key],
                    "total": po.invoice_total or 0, "invoice_no": po.invoice_no or "",
                    "bilty_no": po.bilty_no or "", "date": str(po.creation or ""),
                })
    if kind in ("all", "website"):
        filters = {"seller_profile": profile_id}
        if status:
            filters["status"] = status
        for co in frappe.get_all("Customer Order", filters=filters,
                                 fields=["name", "status", "total", "buyer_profile", "creation"],
                                 order_by="creation desc", limit_page_length=limit):
            buyer = frappe.db.get_value("BuyerProfile", co.buyer_profile, "full_name") or "گاہک / Customer"
            rows.append({
                "kind": "website", "name": co.name, "role": "seller", "status": co.status,
                "counterparty": buyer, "total": co.total or 0, "invoice_no": "", "bilty_no": "",
                "date": str(co.creation or ""),
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]
