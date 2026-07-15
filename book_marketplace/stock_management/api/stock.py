import frappe
from frappe import _


@frappe.whitelist()
def get_by_branch(branch_id):
    entries = frappe.get_all(
        "StockLedger",
        filters={"branch_location": branch_id},
        fields=["name", "book", "volume", "quantity", "state", "reference_type", "reference_name"],
        order_by="modified desc",
    )
    for e in entries:
        book = frappe.db.get_value("Book", e.book, ["title", "author", "book_type"], as_dict=True)
        e.book_title = book.title if book else ""
        e.book_author = book.author if book else ""
    return entries


@frappe.whitelist()
def get_summary(profile_id):
    locations = frappe.get_all("BranchLocation", filters={"business_profile": profile_id, "is_active": 1}, pluck="name")
    if not locations:
        return []
    result = []
    for loc in locations:
        available = sum(
            frappe.get_all(
                "StockLedger",
                filters={"branch_location": loc, "state": "Available"},
                pluck="quantity",
            )
        )
        reserved = sum(
            frappe.get_all(
                "StockLedger",
                filters={"branch_location": loc, "state": "Reserved"},
                pluck="quantity",
            )
        )
        loc_name = frappe.db.get_value("BranchLocation", loc, "branch_name")
        result.append({"branch_id": loc, "branch_name": loc_name, "available": available, "reserved": reserved})
    return result


@frappe.whitelist()
def get_volume_stock(book_id, profile_id=None):
    filters = {"book": book_id, "state": "Available"}
    if profile_id:
        filters["business_profile"] = profile_id
    entries = frappe.get_all(
        "StockLedger",
        filters=filters,
        fields=["volume", "quantity", "branch_location"],
    )
    volume_stock = {}
    for e in entries:
        vol = e.volume or "Complete Set"
        if vol not in volume_stock:
            volume_stock[vol] = 0
        volume_stock[vol] += e.quantity
    return volume_stock


@frappe.whitelist()
def transfer(from_branch, to_branch, book, quantity, volume=None):
    doc = frappe.get_doc({
        "doctype": "StockTransfer",
        "from_branch": from_branch,
        "to_branch": to_branch,
        "book": book,
        "volume": volume,
        "quantity": quantity,
        "status": "Draft",
    })
    doc.insert()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def confirm_receive(transfer_id):
    doc = frappe.get_doc("StockTransfer", transfer_id)
    doc.status = "Received"
    doc.received_on = frappe.utils.today()
    doc.save()
    return {"name": doc.name, "status": doc.status}
