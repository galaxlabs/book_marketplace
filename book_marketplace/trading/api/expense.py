"""Expenses ledger per business profile: salaries, warehouse rent, electricity, gas, transport and other costs.
Monthly report: totals per category for one month (YYYY-MM). Owner only. Not part of the khata or the stock."""
import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, getdate, nowdate

from book_marketplace.auth.api.auth import _assert_profile_owner

DEFAULT_CATEGORIES = ("Salaries", "Warehouse Rent", "Electricity", "Gas", "Transport", "Other")
CATEGORIES = DEFAULT_CATEGORIES  # kept for the panel's suggestion list


def _clean_category(category):
    """A shop may use its own department names. A name that matches a default (any case) uses the default."""
    name = " ".join((category or "").split())
    if not name:
        frappe.throw(_("Write the expense category."))
    if len(name) > 60:
        frappe.throw(_("Category name is too long (60 characters at most)."))
    for c in DEFAULT_CATEGORIES:
        if c.lower() == name.lower():
            return c
    return name


@frappe.whitelist()
def add_expense(business_profile, category, amount, expense_date=None, note=None):
    _assert_profile_owner(business_profile)
    category = _clean_category(category)
    if flt(amount) <= 0:
        frappe.throw(_("Amount must be greater than zero."))
    doc = frappe.get_doc({
        "doctype": "Shop Expense", "business_profile": business_profile, "category": category,
        "amount": flt(amount), "expense_date": getdate(expense_date) if expense_date else getdate(nowdate()),
        "note": (note or "").strip()[:300],
    })
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "category": doc.category, "amount": doc.amount, "expense_date": str(doc.expense_date)}


def _month_range(month):
    """'YYYY-MM' to (first day, last day) as dates. Empty means the current month."""
    month = (month or nowdate()[:7]).strip()
    first = getdate(month + "-01")
    last = add_days(add_months(first, 1), -1)
    return first, last


@frappe.whitelist()
def list_expenses(business_profile, month=None):
    _assert_profile_owner(business_profile)
    first, last = _month_range(month)
    return frappe.get_all(
        "Shop Expense",
        filters={"business_profile": business_profile, "expense_date": ["between", [first, last]]},
        fields=["name", "category", "amount", "expense_date", "note"],
        order_by="expense_date desc, creation desc",
        limit_page_length=500,
    )


@frappe.whitelist()
def expense_summary(business_profile, month=None):
    """Monthly report: total per category and the grand total for the month."""
    _assert_profile_owner(business_profile)
    first, last = _month_range(month)
    rows = frappe.get_all(
        "Shop Expense",
        filters={"business_profile": business_profile, "expense_date": ["between", [first, last]]},
        fields=["category", "amount"], limit_page_length=0,
    )
    per = {c: 0.0 for c in DEFAULT_CATEGORIES}
    for r in rows:
        per[r.category] = per.get(r.category, 0.0) + flt(r.amount)
    own = sorted(c for c in per if c not in DEFAULT_CATEGORIES)
    order = list(DEFAULT_CATEGORIES) + own
    return {
        "month": str(first)[:7],
        "categories": [{"category": c, "amount": round(per[c], 2)} for c in order],
        "total": round(sum(per.values()), 2),
    }
