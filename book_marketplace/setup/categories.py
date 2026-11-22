"""Rebuild the Book Type or Title Type list of Book Categories from books_master_catalog_v2.json.

Dry run first (nothing is written):
    frappe console: from book_marketplace.setup.categories import rebuild_types; rebuild_types(path, "Book Type")
Pass apply=1 to write. Old rows of that type go (the root row stays); book links to them are removed,
syllabus links to them are cleared (the counts are reported first).
"""

import json

import frappe

ROOTS = {"Book Type": "Book Types", "Title Type": "Title Types"}


def _chunks(names, size=300):
    for i in range(0, len(names), size):
        yield names[i : i + size]


def _count_in(table, column, names):
    total = 0
    for part in _chunks(names):
        total += frappe.db.sql(f"SELECT COUNT(*) FROM `tab{table}` WHERE `{column}` IN %(n)s", {"n": tuple(part)})[0][0]
    return total


def _plan(data, kind):
    rows = []
    if kind == "Book Type":
        for i, x in enumerate(data["book_types"]):
            rows.append({"en": x["title_en"].strip(), "ur": x.get("title_ur", "").strip(), "parent": None, "sort": i, "group": 0})
    else:
        for i, g in enumerate(data["title_and_product_types"]):
            rows.append({"en": g["title_en"].strip(), "ur": g.get("title_ur", "").strip(), "parent": None, "sort": i, "group": 1})
            top = len(rows) - 1
            for j, it in enumerate(g.get("items", [])):
                rows.append({"en": it["title_en"].strip(), "ur": it.get("title_ur", "").strip(), "parent": top, "sort": j, "group": 0})
    return rows


@frappe.whitelist()
def rebuild_types(path, kind, apply=0):
    apply = int(apply or 0)
    root = frappe.db.get_value(
        "Book Category", {"category_name": ROOTS[kind], "category_type": kind, "parent_book_category": ["is", "not set"]}, "name"
    )
    if not root:
        frappe.throw(f"The root row '{ROOTS[kind]}' ({kind}) was not found.")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = _plan(data, kind)
    old = [r.name for r in frappe.get_all("Book Category", filters={"category_type": kind, "name": ["!=", root]}, fields=["name"])]
    links = _count_in("BookClassification", "category", old) if old else 0
    syllabus = sum(_count_in("Syllabus Entry", f, old) for f in ("board", "stage", "darja")) if old else 0
    summary = {"kind": kind, "old_rows": len(old), "old_book_links": links, "syllabus_links_to_old": syllabus,
               "new_rows": len(rows), "new_top_level": sum(1 for r in rows if r["parent"] is None), "applied": bool(apply)}
    if not apply:
        return summary
    for part in _chunks(old):
        frappe.db.sql("DELETE FROM `tabBookClassification` WHERE `category` IN %(n)s", {"n": tuple(part)})
    for f in ("board", "stage", "darja"):
        for part in _chunks(old):
            frappe.db.sql(f"UPDATE `tabSyllabus Entry` SET `{f}`=NULL WHERE `{f}` IN %(n)s", {"n": tuple(part)})
    for part in _chunks(old):
        frappe.db.sql("DELETE FROM `tabBook Category` WHERE `name` IN %(n)s", {"n": tuple(part)})
    made = {}
    for idx, r in enumerate(rows):
        parent_name = root if r["parent"] is None else made[r["parent"]]
        doc = frappe.get_doc({
            "doctype": "Book Category", "category_name": r["en"], "category_name_ur": r["ur"], "category_type": kind,
            "parent_book_category": parent_name, "is_group": r["group"], "sort_order": r["sort"],
        })
        doc.insert(ignore_permissions=True)
        made[idx] = doc.name
    frappe.db.commit()
    summary["inserted"] = len(made)
    return summary
