import frappe
from frappe import _


@frappe.whitelist()
def create(buyer, seller, items, order_type="Purchase", delivery_method=None):
    doc = frappe.get_doc({
        "doctype": "InternalOrder",
        "order_type": order_type,
        "buyer_profile": buyer,
        "seller_profile": seller,
        "delivery_method": delivery_method,
        "status": "Draft",
        "items": [
            {
                "book": item["book"],
                "volume": item.get("volume"),
                "quantity": item["quantity"],
                "rate": item["rate"],
                "notes": item.get("notes", ""),
            }
            for item in items
        ],
    })
    doc.insert()
    return {"name": doc.name, "status": doc.status, "total_amount": doc.total_amount}


@frappe.whitelist()
def list_by_profile(profile_id, status=None, limit=20, offset=0):
    filters = frappe._dict()
    filters.buyer_profile = profile_id
    if status:
        filters.status = status
    orders = frappe.get_all(
        "InternalOrder",
        filters=filters,
        fields=["name", "order_type", "seller_profile", "status", "total_amount", "order_date", "creation"],
        limit=limit,
        offset=offset,
        order_by="creation desc",
    )
    for o in orders:
        seller = frappe.db.get_value("BusinessProfile", o.seller_profile, "business_name")
        o.seller_name = seller
    return orders


@frappe.whitelist()
def approve(order_id):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Approved"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def reject(order_id, reason=None):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Rejected"
    if reason:
        doc.notes = reason
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def mark_dispatched(order_id, transport_details=None):
    doc = frappe.get_doc("InternalOrder", order_id)
    doc.status = "Dispatched"
    if transport_details:
        doc.transport_details = transport_details
    doc.save()
    return {"name": doc.name, "status": doc.status}
