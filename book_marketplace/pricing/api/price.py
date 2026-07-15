import frappe
from frappe import _


@frappe.whitelist()
def update_mrp(book_id, publisher_id, new_mrp):
    book = frappe.get_doc("Book", book_id)
    old_mrp = book.mrp
    book.mrp = new_mrp
    book.save()
    log = frappe.get_doc({
        "doctype": "PriceUpdateLog",
        "book": book_id,
        "publisher": publisher_id,
        "old_mrp": old_mrp,
        "new_mrp": new_mrp,
        "changed_by": frappe.session.user,
        "changed_on": frappe.utils.now(),
    })
    log.insert()
    return {"book": book_id, "old_mrp": old_mrp, "new_mrp": new_mrp, "log_name": log.name}


@frappe.whitelist()
def get_policy(book_id, seller_id=None):
    policies = frappe.get_all(
        "PricePolicy",
        filters={"book": book_id, "is_active": 1},
        fields=["name", "publisher_profile", "mrp", "min_seller_price", "max_discount_pct", "allow_discount_below_min"],
    )
    for p in policies:
        name = frappe.db.get_value("BusinessProfile", p.publisher_profile, "business_name")
        p.publisher_name = name
    return policies


@frappe.whitelist()
def get_alerts(profile_id, since=None):
    filters = frappe._dict()
    if since:
        filters.changed_on = [">=", since]
    logs = frappe.get_all(
        "PriceUpdateLog",
        filters=filters,
        fields=["name", "book", "publisher", "old_mrp", "new_mrp", "changed_on"],
        order_by="changed_on desc",
        limit=50,
    )
    for l in logs:
        book = frappe.db.get_value("Book", l.book, ["title", "author"], as_dict=True)
        l.book_title = book.title if book else ""
        l.book_author = book.author if book else ""
        publisher = frappe.db.get_value("BusinessProfile", l.publisher, "business_name")
        l.publisher_name = publisher
    return logs
