"""Admin dashboard numbers: who joined, and how many shops, publishers, distributors and agents."""
import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

from book_marketplace.auth.api.auth import _assert_admin

PROFILE_KINDS = ["Bookseller", "Publisher", "Distributor", "Agent"]


def _count_users(since=None):
    filters = {"name": ["not in", ["Guest", "Administrator"]], "user_type": "Website User"}
    if since:
        filters["creation"] = [">=", since]
    return frappe.db.count("User", filters)


@frappe.whitelist()
def summary(days=30):
    _assert_admin()
    days = max(1, min(int(days), 365))
    today = getdate(nowdate())
    out = {
        "users": {"total": _count_users(), "today": _count_users(today),
                  "last_7": _count_users(add_days(today, -7)), "last_30": _count_users(add_days(today, -30))},
        "profiles": {},
        "series": [],
    }
    for kind in PROFILE_KINDS:
        out["profiles"][kind] = {
            "total": frappe.db.count("BusinessProfile", {"profile_type": kind}),
            "approved": frappe.db.count("BusinessProfile", {"profile_type": kind, "status": "Approved"}),
            "pending": frappe.db.count("BusinessProfile", {"profile_type": kind, "status": "Pending"}),
        }
    out["buyers"] = frappe.db.count("BuyerProfile")
    # new accounts per day, last N days
    start = add_days(today, -(days - 1))
    rows = frappe.db.sql("""
        select date(creation) as d, count(*) as c from `tabUser`
        where creation >= %s and name not in ('Guest', 'Administrator') and user_type = 'Website User'
        group by date(creation) order by d
    """, (start,), as_dict=True)
    by_day = {str(r.d): int(r.c) for r in rows}
    for i in range(days):
        d = add_days(start, i)
        out["series"].append({"date": str(d), "new_accounts": by_day.get(str(d), 0)})
    return out
