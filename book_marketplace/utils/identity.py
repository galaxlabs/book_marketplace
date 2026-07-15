import frappe


def find_duplicate_book(title=None, author=None, publisher=None, isbn=None):
    if isbn:
        existing = frappe.db.get_value("Book", {"isbn": isbn}, "name")
        if existing:
            return existing, "ISBN match"
    if title and author:
        filters = {"title": title, "author": author}
        if publisher:
            filters["publisher"] = publisher
        existing = frappe.db.get_value("Book", filters, "name")
        if existing:
            return existing, "Title + Author match"
    return None, None


def suggest_merge(source_book_id, target_book_id):
    source = frappe.get_doc("Book", source_book_id)
    target = frappe.get_doc("Book", target_book_id)
    return {
        "source": source.title,
        "target": target.title,
        "suggestion": "Review manually before merging",
    }
