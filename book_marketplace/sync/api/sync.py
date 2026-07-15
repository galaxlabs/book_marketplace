import json
import frappe
from frappe import _


@frappe.whitelist()
def push(profile_id, actions):
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
    result = {}
    modules = ["book", "internal_order", "stock_ledger", "price_policy", "price_update_log"]
    for doctype in modules:
        doctype_title = " ".join(p.capitalize() for p in doctype.split("_"))
        filters = {"modified": [">", last_sync]} if last_sync else {}
        records = frappe.get_all(doctype_title, filters=filters, limit=100)
        result[doctype] = records
    return result


@frappe.whitelist()
def status(profile_id):
    pending = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Pending"})
    failed = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Failed"})
    synced = frappe.db.count("SyncQueue", {"business_profile": profile_id, "status": "Synced"})
    return {"pending": pending, "failed": failed, "synced": synced}
