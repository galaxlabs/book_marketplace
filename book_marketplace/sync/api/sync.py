import json
import frappe
from frappe import _


@frappe.whitelist()
def push(profile_id, actions):
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, "catalog.edit")
    if isinstance(actions, str):
        actions = json.loads(actions)
    results = []
    for action in actions:
        record = frappe.get_doc({
            "doctype": "SyncQueue",
            "business_profile": profile_id,
            "action": action.get("action", "POST"),
            "endpoint": action.get("endpoint"),
            "payload": json.dumps(action.get("payload", {})),
            "status": "Pending",
        })
        record.insert()
        results.append({"queue_name": record.name, "status": "Pending"})
    return results


@frappe.whitelist()
def pull(profile_id, last_sync=None):
    """Records this business may see: its own books and the books in its catalog, its orders (as buyer or seller),
    its stock, its price rules and price changes. Nothing of other businesses."""
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, "catalog.view")
    base = {"modified": [">", last_sync]} if last_sync else {}

    catalog_books = frappe.get_all("Business Catalog Item", filters={"business_profile": profile_id},
                                   pluck="book", limit_page_length=0)
    book_names = list(set(catalog_books))
    own = frappe.get_all("Book", filters={**base, "publisher": profile_id}, pluck="name", limit_page_length=0)
    book_names = list(set(book_names) | set(own))

    result = {}
    result["book"] = frappe.get_all("Book", filters={**base, "name": ["in", book_names or [""]]},
                                    fields=["*"], order_by="modified asc", limit=100)
    result["internal_order"] = frappe.get_all(
        "InternalOrder", filters=base, fields=["*"], order_by="modified asc", limit=100,
        or_filters=[{"buyer_profile": profile_id}, {"seller_profile": profile_id}])
    result["stock_ledger"] = frappe.get_all("StockLedger", filters={**base, "business_profile": profile_id},
                                            fields=["*"], order_by="modified asc", limit=100)
    result["price_policy"] = frappe.get_all(
        "PricePolicy", filters={**base, "book": ["in", book_names or [""]]}, fields=["*"],
        order_by="modified asc", limit=100)
    result["price_update_log"] = frappe.get_all("PriceUpdateLog", filters={**base, "publisher": profile_id},
                                                fields=["*"], order_by="modified asc", limit=100)
    return result


@frappe.whitelist()
def status(profile_id):
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, "catalog.view")
    pending = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Pending"})
    failed = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Failed"})
    synced = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Synced"})
    return {"pending": pending, "failed": failed, "synced": synced}
