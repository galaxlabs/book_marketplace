"""Dashboard statistics for one business profile (owner or administrator only).

Orders are read from the tables the Orders page uses: Purchase Order Request (between businesses) and
Customer Order (website orders). Money received is read from the khata. The old InternalOrder and
PaymentTransaction tables are still counted, so older data is not lost from the totals."""
from collections import Counter, OrderedDict

import frappe
from frappe.utils import add_months, flt, getdate, nowdate

from book_marketplace.auth.api.auth import _assert_profile_owner

CLOSED_STATUSES = {"Cancelled", "Rejected", "Expired"}


def _month_key(value):
    return getdate(value).strftime("%Y-%m")


@frappe.whitelist()
def get_dashboard_stats(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "reports.view")

    # books and other items this business holds as its own: published by it, or added by it
    books = frappe.db.sql(
        "select count(*) from tabBook where publisher=%(p)s or owner_profile=%(p)s", {"p": profile_id}
    )[0][0]
    catalog_items = frappe.db.count("BusinessCatalogItem", {"business_profile": profile_id, "is_active": 1})
    bank_accounts = frappe.db.count("BusinessBankAccount", {"business_profile": profile_id, "is_active": 1})

    # orders this business received: from other businesses, from the website, and older internal orders
    received = []
    for o in frappe.get_all("Purchase Order Request", filters={"seller_profile": profile_id},
                            fields=["status", "creation", "total"], limit_page_length=0):
        received.append({"status": o.status or "Sent", "date": o.creation, "value": flt(o.total)})
    for o in frappe.get_all("Customer Order", filters={"seller_profile": profile_id},
                            fields=["status", "creation", "total"], limit_page_length=0):
        received.append({"status": o.status or "Awaiting Payment", "date": o.creation, "value": flt(o.total)})
    for o in frappe.get_all("InternalOrder", filters={"seller_profile": profile_id},
                            fields=["status", "order_date", "total_amount"], limit_page_length=0):
        received.append({"status": o.status or "Draft", "date": o.order_date, "value": flt(o.total_amount)})
    website_orders = frappe.db.count("Customer Order", {"seller_profile": profile_id})

    # orders this business placed with others (its purchases)
    purchases = frappe.get_all("Purchase Order Request", filters={"buyer_profile": profile_id},
                               fields=["status", "total"], limit_page_length=0)

    order_status = Counter(o["status"] for o in received)

    # last six months of order value, without cancelled, rejected and expired orders
    start = add_months(getdate(nowdate()).replace(day=1), -5)
    months = OrderedDict((add_months(start, i).strftime("%Y-%m"), 0.0) for i in range(6))
    for o in received:
        if o["status"] in CLOSED_STATUSES or not o["date"]:
            continue
        key = _month_key(o["date"])
        if key in months:
            months[key] += o["value"]

    # money: khata entries by type, plus verified older payment records
    khata = frappe.get_all("Khata Entry", filters={"business_profile": profile_id},
                           fields=["entry_type", "amount"], limit_page_length=0)
    payments_old = frappe.get_all("PaymentTransaction", filters={"seller_profile": profile_id},
                                  fields=["status", "amount"], limit_page_length=0)
    payment_status = Counter(k.entry_type or "Other" for k in khata)
    for p in payments_old:
        payment_status[p.status or "Pending"] += 1
    received_money = sum(flt(k.amount) for k in khata if k.entry_type == "Payment Received")
    received_money += sum(flt(p.amount) for p in payments_old if p.status == "Verified")

    return {
        "totals": {
            "books": books,
            "catalog_items": catalog_items,
            "bank_accounts": bank_accounts,
            "orders": len(received),
            "website_orders": website_orders,
            "purchases": len(purchases),
            "order_value": round(sum(o["value"] for o in received if o["status"] not in CLOSED_STATUSES), 2),
            "payments_verified": round(received_money, 2),
        },
        "orders_by_status": dict(order_status),
        "payments_by_status": dict(payment_status),
        "monthly_order_value": [{"month": k, "value": round(v, 2)} for k, v in months.items()],
    }
