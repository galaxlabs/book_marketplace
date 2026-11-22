"""Rebuild the Department tree of Book Categories from a taxonomy file (books_taxonomy.json).

Run with a dry run first (nothing is written):

    bench --site <site> execute book_marketplace.setup.departments.rebuild --kwargs "{'path': '/home/dg/books_taxonomy.json'}"
    bench --site <site> execute book_marketplace.setup.departments.rebuild --kwargs "{'path': '/home/dg/books_taxonomy.json', 'apply': 1}"

What it does with `apply`:
  1. removes the book links (Book Classification) that point at the old department rows,
  2. removes the old department rows (the root "Departments" row stays as the container),
  3. inserts the new tree: department -> sub-department -> boards / levels / programs / categories / subjects ...

Lower levels that are plain text become child departments with the English text as the name.
`boards_filter` is a list of board names used as a filter; it is not a department and is reported, not stored.
"""

import json

import frappe

ROOT_NAME = "Departments"
CHILD_LISTS = ("boards", "levels", "programs", "categories", "subjects", "courses", "exams", "parts", "grades")


def _root():
    name = frappe.db.get_value(
        "Book Category", {"category_name": ROOT_NAME, "category_type": "Department", "parent_book_category": ["is", "not set"]}, "name"
    )
    if not name:
        frappe.throw("The root row 'Departments' (type Department) was not found.")
    return name


def _text(v):
    return str(v).strip() if v not in (None, "") else ""


def _node_rows(item, parent, sort, rows, skipped):
    """Turn one taxonomy item into rows: (name_en, name_ur, parent_index, description, is_group)."""
    if isinstance(item, str):
        rows.append({"en": item.strip(), "ur": "", "parent": parent, "desc": "", "sort": sort, "group": 0})
        return
    if not isinstance(item, dict):
        skipped.append(repr(item))
        return
    en = _text(item.get("title_en")) or _text(item.get("name")) or _text(item.get("stage")) or _text(item.get("id"))
    ur = _text(item.get("title_ur"))
    extra = [f"years: {item['years']}"] if "years" in item else []
    kids = []
    for k in CHILD_LISTS:
        if isinstance(item.get(k), list):
            kids.append((k, item[k]))
    me = len(rows)
    rows.append({"en": en, "ur": ur, "parent": parent, "desc": "; ".join(extra), "sort": sort, "group": 1 if kids else 0})
    for k, items in kids:
        for i, child in enumerate(items):
            _node_rows(child, me, i, rows, skipped)


def _plan(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows, skipped = [], []
    subs = []
    for i, dep in enumerate(data["departments"]):
        rows.append({"en": _text(dep.get("title_en")), "ur": _text(dep.get("title_ur")), "parent": None, "desc": "", "sort": i, "group": 1})
        top = len(rows) - 1
        for j, sub in enumerate(dep.get("sub_departments", [])):
            _node_rows(sub, top, j, rows, skipped)
    filters = data["departments"][0].get("boards_filter") if data["departments"] else None
    return rows, skipped, filters


def _chunks(names, size=300):
    for i in range(0, len(names), size):
        yield names[i : i + size]


def _count_in(table, column, names):
    total = 0
    for part in _chunks(names):
        total += frappe.db.sql(f"SELECT COUNT(*) FROM `tab{table}` WHERE `{column}` IN %(n)s", {"n": tuple(part)})[0][0]
    return total


def _old_department_names(root):
    return [r.name for r in frappe.get_all("Book Category", filters={"category_type": "Department", "name": ["!=", root]}, fields=["name"])]


@frappe.whitelist()
def rebuild(path, apply=0):
    apply = int(apply or 0)
    root = _root()
    rows, skipped, filters = _plan(path)
    old = _old_department_names(root)
    links = _count_in("BookClassification", "category", old) if old else 0
    syllabus = 0
    if old:
        for f in ("board", "stage", "darja"):
            syllabus += _count_in("Syllabus Entry", f, old)
    summary = {
        "old_departments": len(old),
        "old_book_links": links,
        "syllabus_links_to_old": syllabus,
        "new_rows": len(rows),
        "new_top_level": sum(1 for r in rows if r["parent"] is None),
        "skipped": skipped[:20],
        "boards_filter_not_imported": len(filters or []),
        "applied": bool(apply),
    }
    if not apply:
        return summary

    # 1. book links to the old departments go; the books keep their subject and title categories
    for part in _chunks(old):
        frappe.db.sql("DELETE FROM `tabBookClassification` WHERE `category` IN %(n)s", {"n": tuple(part)})
    # 2. syllabus entries that point at an old department are cleared (reported above)
    if old and syllabus:
        for f in ("board", "stage", "darja"):
            frappe.db.sql(f"UPDATE `tabSyllabus Entry` SET `{f}`=NULL WHERE `{f}` IN %(names)s", {"names": tuple(old)})
    # 3. old department rows go, in batches
    for part in _chunks(old):
        frappe.db.sql("DELETE FROM `tabBook Category` WHERE `name` IN %(n)s", {"n": tuple(part)})
    # 4. the new tree: rows keep their parent position while inserting
    made = {}
    for idx, r in enumerate(rows):
        if r["parent"] is None:
            parent_name = root
        else:
            parent_name = made[r["parent"]]
        doc = frappe.get_doc(
            {
                "doctype": "Book Category",
                "category_name": r["en"] or "-",
                "category_name_ur": r["ur"],
                "category_type": "Department",
                "parent_book_category": parent_name,
                "is_group": r["group"],
                "description": r["desc"],
                "sort_order": r["sort"],
            }
        )
        doc.insert(ignore_permissions=True)
        made[idx] = doc.name
    frappe.db.commit()
    summary["inserted"] = len(made)
    return summary
