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
        kids += frappe.get_all(
            "Book Category", filters={"section": ["in", frontier]},
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
    """Who may add categories, subjects and types: publishers, booksellers, distributors, and admin."""
    from book_marketplace.auth.api.auth import _assert_profile_owner, _is_admin

    if _is_admin():
        return
    if not profile_id or frappe.db.get_value("BusinessProfile", profile_id, "profile_type") not in ("Publisher", "Bookseller", "Distributor"):
        frappe.throw(_("Only a business account or admin can do this."), frappe.PermissionError)
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
def add_book_category(profile_id, category_name, category_type="Subject", parent=None, category_name_ur=None, section=None):
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
        "section": (section or None) if not parent else None,
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "created"}


@frappe.whitelist()
def set_book_classification(profile_id, book_id, categories=None, is_bound=0, is_set=0,
                            volume_count=None, part_count=None):
    """Owner (publisher or the bookseller who added the book) sets the categories and limits."""
    book = frappe.get_doc("Book", book_id)
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Login required."), frappe.PermissionError)
    _check_publisher_or_admin(profile_id)
    from book_marketplace.auth.api.auth import _is_admin
    if not _is_admin() and profile_id not in (book.publisher, book.get("owner_profile")):
        frappe.throw(_("Only the publisher or the bookseller who added this book can change it."), frappe.PermissionError)
    set_rows(book, categories if isinstance(categories, list) else [c for c in (categories or "").split(",")])
    book.is_bound = 1 if int(is_bound or 0) else 0
    book.is_set = 1 if int(is_set or 0) else 0
    book.volume_count = int(volume_count) if volume_count not in (None, "") else None
    book.part_count = int(part_count) if part_count not in (None, "") else None
    book.save(ignore_permissions=True)
    return {"name": book.name, "categories": [r.category for r in book.classifications]}
@frappe.whitelist(allow_guest=True)
def section_overview(lang=None):
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
            children = frappe.get_all(
                "Book Category", filters={"parent_book_category": r.name, "category_type": "Subject"},
                fields=["name", "category_name"], order_by="creation asc", limit_page_length=0,
            )
            subjects.append({
                "name": r.name, "category_name": r.category_name, "category_name_ur": r.category_name_ur,
                "books": len(book_names_for_categories([r.name])),
                "label": label(r.category_name, r.category_name_ur, lang),
                "children": [{"name": c.name, "category_name": c.category_name,
                              "label": label(c.category_name, None, lang)} for c in children],
            })
        if not subjects:
            continue
        out.append({
            "name": sec.name, "category_name": sec.category_name, "category_name_ur": sec.category_name_ur,
            "label": label(sec.category_name, sec.category_name_ur, lang),
            "books": len({b for sj in subjects for b in book_names_for_categories([sj["name"]])}),
            "subjects": subjects,
        })
    return out



def label(name_en, name_ur=None, lang=None):
    """Name in the reader's language, from Frappe's Translation doctype. Falls back to the stored Urdu name."""
    if not lang:
        return name_en
    translated = frappe._(name_en, lang=lang)
    if translated == name_en and lang == "ur" and name_ur:
        return name_ur
    return translated


@frappe.whitelist(allow_guest=True)
def department_tree(lang=None):
    """شعبہ جات: the education tree, nested, with translated labels."""
    rows = frappe.get_all(
        "Book Category", filters={"category_type": "Department"},
        fields=["name", "category_name", "category_name_ur", "parent_book_category", "section", "sort_order"],
        order_by="category_name asc", limit_page_length=0,
    )
    kids = {}
    for r in rows:
        kids.setdefault(r.parent_book_category or "", []).append(r)
    for sibs in kids.values():
        # an explicit order (boards, stages, darajat) comes first, in that order; the rest by name
        sibs.sort(key=lambda r: (not r.sort_order, r.sort_order or 0, r.category_name))

    def build(parent, inherited=None):
        out = []
        for r in kids.get(parent, []):
            sec = r.section or inherited
            out.append({
                "name": r.name, "category_name": r.category_name,
                "label": label(r.category_name, r.category_name_ur, lang),
                "section": sec,
                "children": build(r.name, sec),
            })
        return out

    return build("")


@frappe.whitelist(allow_guest=True)
def filter_trees(lang=None):
    """The three public filters, each a nested tree {name, label, children}:
    department (شعبہ → ذیلی شعبہ ...), subject (موضوع = section → ذیلی موضوع ...), form (قسم → ذیلی قسم)."""
    rows = frappe.get_all(
        "Book Category",
        fields=["name", "category_name", "category_name_ur", "parent_book_category", "section", "sort_order", "category_type"],
        limit_page_length=0,
    )
    kids = {}
    for r in rows:
        kids.setdefault(r.parent_book_category or "", []).append(r)
    for sibs in kids.values():
        sibs.sort(key=lambda r: (not r.sort_order, r.sort_order or 0, label(r.category_name, r.category_name_ur, lang)))

    def build(parent):
        return [{"name": r.name, "label": label(r.category_name, r.category_name_ur, lang), "children": build(r.name)}
                for r in kids.get(parent, [])]

    def root(name, ctype):
        return next((r.name for r in kids.get("", []) if r.category_name == name and r.category_type == ctype), None)

    dep, forms, types = root("Departments", "Department"), root("Title Types", "Title Type"), root("Book Types", "Book Type")
    subject_roots = {}
    for r in kids.get("", []):
        if r.category_type == "Subject" and r.section:
            subject_roots.setdefault(r.section, []).append(r)
    subject = []
    for sec in kids.get(types, []):
        roots = sorted(subject_roots.get(sec.name, []), key=lambda r: label(r.category_name, r.category_name_ur, lang))
        if roots:
            subject.append({"name": sec.name, "label": label(sec.category_name, sec.category_name_ur, lang),
                            "children": [{"name": r.name, "label": label(r.category_name, r.category_name_ur, lang),
                                          "children": build(r.name)} for r in roots]})
    return {"department": build(dep) if dep else [], "subject": subject, "form": build(forms) if forms else []}
