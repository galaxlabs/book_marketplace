import frappe


def notify_price_update(book_id, publisher_id, old_mrp, new_mrp):
    log_name = frappe.db.get_value(
        "PriceUpdateLog",
        {"book": book_id, "new_mrp": new_mrp},
        "name",
        order_by="creation desc",
    )
    sellers = frappe.get_all(
        "StockLedger",
        filters={"book": book_id, "state": "Available", "quantity": [">", 0]},
        distinct=1,
        pluck="business_profile",
    )
    notifications = []
    for seller in sellers:
        if seller == publisher_id:
            continue
        notifications.append({"profile": seller, "book": book_id, "log": log_name})
    return {"notified_count": len(notifications), "sellers": notifications}
