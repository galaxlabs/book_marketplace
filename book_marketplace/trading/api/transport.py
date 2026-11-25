"""Transport tracking: every order this shop dispatched (as seller) or is receiving (as buyer), with the transporter,
bilty number and rider. Search by bilty number, transporter or rider name. Only the shop's own orders."""
import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner


@frappe.whitelist()
def track_dispatches(profile_id, query=None, limit=100):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "orders.view")
    text = " ".join((query or "").split())
    limit = min(frappe.utils.cint(limit) or 100, 300)
    base = {"dispatched_on": ["is", "set"]}
    or_filters = None
    if text:
        like = f"%{text}%"
        or_filters = {"bilty_no": ["like", like], "transporter": ["like", like], "rider_name": ["like", like]}
    rows = []
    for role, key in (("seller", "seller_profile"), ("buyer", "buyer_profile")):
        found = frappe.get_all(
            "Purchase Order Request",
            filters={**base, key: profile_id},
            or_filters=or_filters,
            fields=["name", "status", "transporter", "bilty_no", "rider_name", "rider_phone",
                    "dispatched_on", "agreed_on", "seller_profile", "buyer_profile"],
            order_by="dispatched_on desc",
            limit_page_length=limit,
        )
        for r in found:
            r["role"] = role
            rows.append(r)
    names = {p.name: p.business_name for p in frappe.get_all(
        "BusinessProfile", filters={"name": ["in", list({r["seller_profile"] for r in rows} | {r["buyer_profile"] for r in rows} or [""])]},
        fields=["name", "business_name"])}
    for r in rows:
        r["seller_name"] = names.get(r["seller_profile"], "")
        r["buyer_name"] = names.get(r["buyer_profile"], "")
        r["counterparty"] = r["buyer_name"] if r["role"] == "seller" else r["seller_name"]
    rows.sort(key=lambda r: str(r.get("dispatched_on") or ""), reverse=True)
    return rows[:limit]
