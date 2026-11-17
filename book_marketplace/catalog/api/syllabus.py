import frappe

from book_marketplace.catalog.api.classification import label


@frappe.whitelist(allow_guest=True)
def get_syllabus(darja, lang=None):
    """Books of one darja in order, grouped by subject, with labels for the board, stage and darja."""
    rows = frappe.get_all(
        "Syllabus Entry", filters={"darja": darja},
        fields=["name", "board", "stage", "darja", "subject_name", "sort_order", "book_title", "portion", "work", "note"],
        order_by="sort_order asc, creation asc", limit_page_length=0,
    )
    def lab(n):
        if not n:
            return None
        r = frappe.db.get_value("Book Category", n, ["category_name", "category_name_ur"], as_dict=True)
        return label(r.category_name, r.category_name_ur, lang) if r else n
    head = rows[0] if rows else frappe._dict(board=None, stage=None)
    return {
        "board": head.board, "board_label": lab(head.board),
        "stage": head.stage, "stage_label": lab(head.stage),
        "darja": darja, "darja_label": lab(darja),
        "entries": rows,
    }


@frappe.whitelist(allow_guest=True)
def syllabus_darajat(board, lang=None):
    """Darajat of a board that have a syllabus, in order."""
    names = frappe.get_all("Syllabus Entry", filters={"board": board}, pluck="darja", distinct=True)
    rows = frappe.get_all("Book Category", filters={"name": ["in", names or [""]]},
                          fields=["name", "category_name", "category_name_ur", "sort_order", "parent_book_category"])
    rows.sort(key=lambda r: (frappe.db.get_value("Book Category", r.parent_book_category, "sort_order") or 0, r.sort_order or 0))
    return [{"name": r.name, "label": label(r.category_name, r.category_name_ur, lang),
             "entries": frappe.db.count("Syllabus Entry", {"darja": r.name})} for r in rows]
