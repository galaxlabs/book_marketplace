import frappe
from frappe.utils import flt

# Books become ERPNext Items so the POS and the Accounts module can bill them.
ITEM_GROUP = "Books"
PRICE_LIST = "Standard Selling"


def _ensure_item_group():
    if not frappe.db.exists("Item Group", ITEM_GROUP):
        frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": ITEM_GROUP,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)


def item_code_for(book_name):
    return f"BOOK-{book_name}"[:140]


def sync_book_to_item(doc, method=None):
    """Create or update the ERPNext Item for a Book. Runs after save."""
    if "erpnext" not in frappe.get_installed_apps():
        return  # sites without ERPNext keep books as they are
    _ensure_item_group()
    item_code = item_code_for(doc.name)
    values = {
        "item_name": (doc.title or doc.name)[:140],
        "description": doc.title or doc.name,
        "item_group": ITEM_GROUP,
        "stock_uom": "Nos",
        "is_stock_item": 0,
        "is_sales_item": 1,
        "is_purchase_item": 0,
        "maintain_stock": 0,
    }
    if frappe.db.exists("Item", item_code):
        item = frappe.get_doc("Item", item_code)
        item.update(values)
        item.save(ignore_permissions=True)
    else:
        item = frappe.get_doc({"doctype": "Item", "item_code": item_code, **values})
        item.insert(ignore_permissions=True)

    rate = flt(doc.get("set_mrp"))
    if rate > 0 and frappe.db.exists("Price List", PRICE_LIST):
        price_name = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": PRICE_LIST}, "name")
        if price_name:
            frappe.db.set_value("Item Price", price_name, "price_list_rate", rate)
        else:
            frappe.get_doc({
                "doctype": "Item Price",
                "item_code": item_code,
                "price_list": PRICE_LIST,
                "price_list_rate": rate,
            }).insert(ignore_permissions=True)
