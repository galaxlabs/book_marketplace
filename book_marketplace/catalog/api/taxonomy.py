import frappe


@frappe.whitelist(allow_guest=True)
def get_book_categories(category_type=None):
    """Flat list of book categories with parent links. Filter by type: Book Type, Title Type, Department."""
    filters = {"category_type": category_type} if category_type else {}
    return frappe.get_all(
        "Book Category",
        filters=filters,
        fields=["name", "category_name", "category_name_ur", "category_type", "parent_book_category", "is_group"],
        order_by="category_type asc, category_name asc",
        limit_page_length=0,
    )
