"""Book classification (stage 2): categories (قسم), subjects (موضوع) with sub-levels, binding flags.

A book can sit in several categories and subjects. Choosing a parent includes all its descendants.
Publishers and admins can add new categories and subjects; publishers can only edit their own books.
"""
import frappe
from frappe import _


def descendants(names):
    """The given category names plus every descendant (children, grandchildren, ...)."""
    seen = {n for n in (names or []) if n}
    frontier = list(seen)
    while frontier:
        kids = frappe.get_all(
            "Book Category", filters={"parent_book_category": ["in", frontier]},
            pluck="name", limit_page_length=0,
        )
        frontier = [k for k in kids if k not in seen]
        seen.update(frontier)
    return seen


def book_names_for_categories(names):
    """Books that carry any of these categories (or any descendant of them)."""
    cats = descendants(names)
    if not cats:
        return []
    rows = frappe.get_all(
        "BookClassification",
        filters={"category": ["in", list(cats)], "parenttype": "Book"},
        pluck="parent", limit_page_length=0,
    )
    return list(set(rows))


def _check_publisher_or_admin(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_owner, _is_admin

    if _is_admin():
        return
    if not profile_id or frappe.db.get_value("BusinessProfile", profile_id, "profile_type") != "Publisher":
        frappe.throw(_("Only publishers or admin can do this."), frappe.PermissionError)
    _assert_profile_owner(profile_id)


def set_rows(book, categories):
    """Replace the book's classification rows with these category names."""
    cleaned = []
    for name in categories or []:
        name = (name or "").strip()
        if name and name not in cleaned:
            if not frappe.db.exists("Book Category", name):
                frappe.throw(_("Unknown category: {0}").format(name))
            cleaned.append(name)
    book.set("classifications", [])
    for name in cleaned:
        book.append("classifications", {"category": name})


@frappe.whitelist()
def add_book_category(profile_id, category_name, category_type="Subject", parent=None, category_name_ur=None):
    """Publisher or admin adds a category or subject. Existing name under the same parent is reused."""
    _check_publisher_or_admin(profile_id)
    category_name = (category_name or "").strip()
    if not category_name:
        frappe.throw(_("Name is required."))
    if category_type not in ("Book Type", "Title Type", "Department", "Subject"):
        frappe.throw(_("Unknown category type."))
    if parent and not frappe.db.exists("Book Category", parent):
        frappe.throw(_("Parent not found."))
    filters = {"category_name": category_name, "category_type": category_type}
    filters["parent_book_category"] = parent if parent else ["is", "not set"]
    existing = frappe.db.get_value("Book Category", filters, "name")
    if existing:
        return {"name": existing, "status": "exists"}
    doc = frappe.get_doc({
        "doctype": "Book Category", "category_name": category_name,
        "category_name_ur": category_name_ur or None, "category_type": category_type,
        "parent_book_category": parent or None, "is_group": 0,
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "created"}


@frappe.whitelist()
def set_book_classification(profile_id, book_id, categories=None, is_bound=0, is_set=0,
                            volume_count=None, part_count=None):
    """Owner publisher (or admin) sets the categories, subjects and limits of a book."""
    book = frappe.get_doc("Book", book_id)
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)
    _check_publisher_or_admin(profile_id)
    from book_marketplace.auth.api.auth import _is_admin
    if not _is_admin() and book.publisher != profile_id:
        frappe.throw(_("Only the publisher of this book can change it."), frappe.PermissionError)
    set_rows(book, categories if isinstance(categories, list) else [c for c in (categories or "").split(",")])
    book.is_bound = 1 if int(is_bound or 0) else 0
    book.is_set = 1 if int(is_set or 0) else 0
    book.volume_count = int(volume_count) if volume_count not in (None, "") else None
    book.part_count = int(part_count) if part_count not in (None, "") else None
    book.save(ignore_permissions=True)
    return {"name": book.name, "categories": [r.category for r in book.classifications]}
@frappe.whitelist()
def section_overview():
    """قسم → موضوعات with book counts. Used by the panel and the public store filters."""
    sections = frappe.get_all(
        "Book Category", filters={"category_type": "Book Type", "parent_book_category": ["is", "set"]},
        fields=["name", "category_name", "category_name_ur"], order_by="category_name asc", limit_page_length=0,
    )
    roots = frappe.get_all(
        "Book Category", filters={"category_type": "Subject", "section": ["is", "set"]},
        fields=["name", "category_name", "category_name_ur", "section"], limit_page_length=0,
    )
    out = []
    for sec in sections:
        subjects = []
        for r in [x for x in roots if x.section == sec.name]:
            subjects.append({
                "name": r.name, "category_name": r.category_name, "category_name_ur": r.category_name_ur,
                "books": len(book_names_for_categories([r.name])),
            })
        subjects.sort(key=lambda x: x["category_name"])
        out.append({
            "name": sec.name, "category_name": sec.category_name, "category_name_ur": sec.category_name_ur,
            "books": len({b for sj in subjects for b in book_names_for_categories([sj["name"]])}),
            "subjects": subjects,
        })
    return out
