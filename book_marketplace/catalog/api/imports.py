import csv
import io

import frappe


@frappe.whitelist()
def import_books_csv(csv_text, publisher_profile=None, dry_run=1):
    """Import books from CSV text.

    Expected columns: title, author, isbn, language, category, genre, mrp,
    book_type, variant_name, binding_type, quality_grade, volume_number.
    """
    rows = list(csv.DictReader(io.StringIO(csv_text or "")))
    results = []
    for index, row in enumerate(rows, start=1):
        title = (row.get("title") or "").strip()
        author = (row.get("author") or "").strip()
        isbn = (row.get("isbn") or "").strip()
        if not title or not author:
            results.append({"row": index, "status": "error", "message": "title and author are required"})
            continue
        duplicate = frappe.db.get_value("Book", {"isbn": isbn}, "name") if isbn else None
        if duplicate:
            results.append({"row": index, "status": "duplicate", "book": duplicate})
            continue
        if dry_run:
            results.append({"row": index, "status": "valid"})
            continue
        doc = frappe.get_doc({
            "doctype": "Book",
            "title": title,
            "author": author,
            "publisher": publisher_profile,
            "isbn": isbn,
            "language": row.get("language"),
            "genre": row.get("genre"),
            "book_type": row.get("book_type") or "Single Volume",
            "set_mrp": row.get("mrp"),
            "global_visibility": 1,
        })
        doc.insert()
        results.append({"row": index, "status": "created", "book": doc.name})
    return {"total": len(rows), "results": results}


@frappe.whitelist()
def import_categories_from_reference():
    categories = [
        "Fiction", "Fantasy", "Classics", "Romance", "Action & Adventure",
        "Historical Fiction", "Horror", "Manga, Comics & Graphic Novel",
        "Mystery, Crime & Detective", "Thriller & Suspense", "Young Adults",
        "Non Fiction", "Self Help", "Biography/Autobiography", "History",
        "Religion & Spirituality", "Business & Management", "Philosophy",
        "Children's Books", "Activity Books", "Home Learning", "Story Books",
        "Urdu", "Afsanay", "Novel", "Urdu Literature", "Shayri", "Safarnama",
        "Mazhab Aur Rohaniyat", "Quran Kareem", "Panj Para", "Panj Surah",
        "Wazaif", "A Levels", "O Levels", "School Books", "Medical Books",
        "CSS Books", "Entry Test", "University Books", "ACCA", "CFA", "IELTS",
        "MDCAT", "MBBS", "Law Books",
    ]
    created = []
    for category in categories:
        if not frappe.db.exists("SubjectMaster", category):
            doc = frappe.get_doc({"doctype": "SubjectMaster", "subject_name": category})
            doc.insert(ignore_permissions=True)
            created.append(category)
    return {"created": created, "count": len(created)}
