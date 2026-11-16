"""One-time bootstrap for a Book Marketplace site.

Run with:
    bench --site <site> execute book_marketplace.setup.bootstrap.run

Each step is idempotent: it skips work that already exists.
"""
import json
import os

import frappe
from frappe.utils import getdate, nowdate

COMPANY = "Book Marketplace"
ABBR = "BM"
POS_PROFILE = "Book Counter"
WALK_IN = "Walk-in Customer"


def run():
    result = {}
    result["erp_setup"] = setup_erp()
    result["item_group"] = ensure_books_item_group()
    result["pos_profile"] = create_pos_profile()
    result["taxonomy"] = seed_taxonomy()
    frappe.db.commit()
    return result


def setup_erp():
    if frappe.db.exists("Company", COMPANY):
        return "exists"
    from erpnext.setup.setup_wizard.setup_wizard import setup_complete

    today = getdate(nowdate())
    fy_year = today.year if today.month >= 7 else today.year - 1
    args = {
        "language": "english",
        "country": "Pakistan",
        "timezone": "Asia/Karachi",
        "currency": "PKR",
        "full_name": "Administrator",
        "email": "admin@bm.digigalaxy.cloud",
        "company_name": COMPANY,
        "company_abbr": ABBR,
        "company_tagline": "Book Marketplace",
        "chart_of_accounts": "Standard",
        "fy_start_date": f"{fy_year}-07-01",
        "fy_end_date": f"{fy_year + 1}-06-30",
        "setup_demo": 0,
    }
    setup_complete(frappe._dict(args))
    return "created"


def ensure_books_item_group():
    from book_marketplace.catalog.item_sync import ITEM_GROUP, _ensure_item_group

    _ensure_item_group()
    return ITEM_GROUP


def create_pos_profile():
    if frappe.db.exists("POS Profile", POS_PROFILE):
        return "exists"
    if not frappe.db.exists("Company", COMPANY):
        return "company missing"

    warehouse = frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0}, "name")
    if not frappe.db.exists("Customer", WALK_IN):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": WALK_IN,
            "customer_type": "Individual",
            "customer_group": "Individual" if frappe.db.exists("Customer Group", "Individual") else frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
            "territory": "All Territories",
        }).insert(ignore_permissions=True)

    company_defaults = frappe.db.get_value("Company", COMPANY, ["write_off_account", "cost_center", "default_income_account", "default_expense_account"], as_dict=True) or {}
    modes = [m for m in ["Cash", "Bank"] if frappe.db.exists("Mode of Payment", m)]
    payments = [{"mode_of_payment": m, "default": 1 if i == 0 else 0} for i, m in enumerate(modes)]

    profile = frappe.get_doc({
        "doctype": "POS Profile",
        "name": POS_PROFILE,
        "company": COMPANY,
        "warehouse": warehouse,
        "currency": "PKR",
        "selling_price_list": "Standard Selling",
        "customer": WALK_IN,
        "write_off_limit": 0,
        "write_off_account": company_defaults.get("write_off_account"),
        "write_off_cost_center": company_defaults.get("cost_center"),
        "income_account": company_defaults.get("default_income_account"),
        "expense_account": company_defaults.get("default_expense_account"),
        "payments": payments,
    })
    profile.insert(ignore_permissions=True)
    return POS_PROFILE


TAXONOMY_FILES = ["book_taxonomy.json", "book_taxonomy_extra.json", "book_taxonomy_extra2.json"]


def seed_taxonomy():
    created = 0
    for name in TAXONOMY_FILES:
        path = os.path.join(os.path.dirname(__file__), "data", name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for root in data["roots"]:
            created += _insert_category(root, parent=None, category_type=root["category_type"])
    return {"created": created}


def _insert_category(node, parent, category_type):
    """Insert a node once (matched by name + type + parent). Returns number of nodes created."""
    filters = {"category_name": node["name"], "category_type": category_type}
    # Root rows store NULL as parent; match them with "is not set" so re-runs do not duplicate.
    filters["parent_book_category"] = parent if parent else ["is", "not set"]
    existing = frappe.db.get_value("Book Category", filters, "name")
    created = 0
    if existing:
        name = existing
    else:
        doc = frappe.get_doc({
            "doctype": "Book Category",
            "category_name": node["name"],
            "category_name_ur": node.get("name_ur"),
            "category_type": category_type,
            "is_group": 1 if node.get("children") else 0,
            "parent_book_category": parent,
        })
        doc.insert(ignore_permissions=True)
        name = doc.name
        created = 1
    for child in node.get("children", []):
        created += _insert_category(child, parent=name, category_type=category_type)
    return created
