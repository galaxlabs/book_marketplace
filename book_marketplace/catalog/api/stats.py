"""Dashboard statistics for one business profile (owner or administrator only)."""
from collections import Counter, OrderedDict

import frappe
from frappe.utils import add_months, flt, getdate, nowdate

from book_marketplace.auth.api.auth import _assert_profile_owner

CLOSED_STATUSES = {"Cancelled", "Rejected"}


def _month_key(value):
    d = getdate(value)
    return d.strftime("%Y-%m")


@frappe.whitelist()
def get_dashboard_stats(profile_id):
    _assert_profile_owner(profile_id)

    books = frappe.db.count("Book", {"publisher": profile_id})
    catalog_items = frappe.db.count("BusinessCatalogItem", {"business_profile": profile_id, "is_active": 1})
    bank_accounts = frappe.db.count("BusinessBankAccount", {"business_profile": profile_id, "is_active": 1})

    orders = frappe.get_all(
        "InternalOrder",
        filters={"seller_profile": profile_id},
        fields=["status", "order_date", "total_amount"],
        limit_page_length=0,
    )
    order_status = Counter(o.status or "Draft" for o in orders)

    # Last six months of order value, excluding cancelled and rejected orders.
    start = add_months(getdate(nowdate()).replace(day=1), -5)
    months = OrderedDict()
    for i in range(6):
        months[add_months(start, i).strftime("%Y-%m")] = 0.0
    for o in orders:
        if o.status in CLOSED_STATUSES or not o.order_date:
            continue
        key = _month_key(o.order_date)
        if key in months:
            months[key] += flt(o.total_amount)

    payments = frappe.get_all(
        "PaymentTransaction",
        filters={"seller_profile": profile_id},
        fields=["status", "amount"],
        limit_page_length=0,
    )
    payment_status = Counter(p.status or "Pending" for p in payments)

    return {
        "totals": {
            "books": books,
            "catalog_items": catalog_items,
            "bank_accounts": bank_accounts,
            "orders": len(orders),
            "order_value": round(sum(flt(o.total_amount) for o in orders if o.status not in CLOSED_STATUSES), 2),
            "payments_verified": round(sum(flt(p.amount) for p in payments if p.status == "Verified"), 2),
        },
        "orders_by_status": dict(order_status),
        "payments_by_status": dict(payment_status),
        "monthly_order_value": [{"month": k, "value": round(v, 2)} for k, v in months.items()],
    }
