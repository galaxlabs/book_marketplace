"""What other businesses owe this business (khata), by business, from the linked entries.

Entries: Credit Given (the other side owes me), Payment Received (they paid me), Credit Taken (I owe them),
Payment Made (I paid them). A party's due = Credit Given - Payment Received (plus or minus what I owe them).
Only entries with a linked business count; old entries without a link are listed separately, to be linked.
"""
import frappe
from frappe import _
from frappe.utils import flt

from book_marketplace.auth.api.auth import _assert_profile_owner


@frappe.whitelist()
def dues_summary(profile_id):
    _assert_profile_owner(profile_id)
    rows = frappe.get_all("Khata Entry", filters={"business_profile": profile_id},
                          fields=["party_profile", "party_name", "entry_type", "amount", "entry_date", "reference"],
                          order_by="entry_date asc", limit_page_length=0)
    by_party, unlinked = {}, []
    for r in rows:
        if not r.party_profile:
            unlinked.append({"party_name": r.party_name, "entry_type": r.entry_type, "amount": flt(r.amount), "reference": r.reference})
            continue
        p = by_party.setdefault(r.party_profile, {"party_profile": r.party_profile, "party_name": r.party_name,
                                                  "given": 0.0, "received": 0.0, "taken": 0.0, "paid": 0.0, "last": None})
        amt = flt(r.amount)
        if r.entry_type == "Credit Given":
            p["given"] += amt
        elif r.entry_type == "Payment Received":
            p["received"] += amt
        elif r.entry_type == "Credit Taken":
            p["taken"] += amt
        elif r.entry_type == "Payment Made":
            p["paid"] += amt
        p["last"] = str(r.entry_date) if r.entry_date else p["last"]
    out = []
    for p in by_party.values():
        due = round((p["given"] - p["received"]) - (p["taken"] - p["paid"]), 2)
        p["due"] = due
        p["business_name"] = frappe.db.get_value("BusinessProfile", p["party_profile"], "business_name") or p["party_name"]
        p["city"] = frappe.db.get_value("BusinessProfile", p["party_profile"], "city") or ""
        out.append(p)
    out.sort(key=lambda x: -x["due"])
    total = round(sum(max(p["due"], 0) for p in out), 2)
    return {"parties": out, "total_to_receive": total, "unlinked": unlinked}


@frappe.whitelist()
def dashboard_today(profile_id):
    """Numbers for the dashboard cards, each linked to the page where the work is done."""
    from frappe.utils import nowdate, get_first_day
    _assert_profile_owner(profile_id)
    today = nowdate()
    received_today = frappe.db.count("Purchase Order Request", {"seller_profile": profile_id, "creation": [">=", today]})
    bought_today = frappe.db.count("Purchase Order Request", {"buyer_profile": profile_id, "creation": [">=", today]})
    web_today = frappe.db.count("Customer Order", {"seller_profile": profile_id, "creation": [">=", today]})
    awaiting_dispatch = frappe.db.count("Purchase Order Request", {"seller_profile": profile_id, "status": "Accepted"})
    awaiting_receipt = frappe.db.count("Purchase Order Request", {"buyer_profile": profile_id, "status": "Dispatched"})
    awaiting_agreement = frappe.db.count("Purchase Order Request", {"seller_profile": profile_id, "status": "Awaiting Agreement"})
    price_changes = frappe.db.count("Catalog Price Change", {"business_profile": profile_id, "seen": 0})
    unread = frappe.db.count("Shop Notification", {"business_profile": profile_id, "is_read": 0})
    policies = frappe.db.count("Trade Discount Policy", {"publisher": profile_id, "status": "Active"})
    month_bought = frappe.db.sql(
        "select coalesce(sum(total), 0) from `tabPurchase Order Request` "
        "where buyer_profile = %s and creation >= %s and status in "
        "('Accepted', 'Dispatched', 'Awaiting Agreement', 'Received', 'Short')",
        (profile_id, get_first_day(today)))[0][0]
    d = dues_summary(profile_id)
    return {"orders_today": received_today + web_today, "bought_today": bought_today, "web_orders_today": web_today,
            "awaiting_dispatch": awaiting_dispatch, "awaiting_receipt": awaiting_receipt,
            "awaiting_agreement": awaiting_agreement, "price_changes": price_changes, "unread": unread,
            "active_policies": policies, "month_bought": float(month_bought or 0),
            "to_receive": d["total_to_receive"]}
