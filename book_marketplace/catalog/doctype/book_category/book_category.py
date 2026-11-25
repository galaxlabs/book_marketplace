import re

import frappe
from frappe.model.document import Document

TYPE_UR = {"Subject": "موضوع", "Department": "شعبہ", "Title Type": "عنوان کی قسم", "Book Type": "قسم"}


def clean_name(s):
    s = (s or "").strip().replace("/", " - ").replace("\\", " - ")
    s = re.sub(r"[<>\"'%#?&]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s[:120].strip()


class BookCategory(Document):
    def autoname(self):
        """Readable ID: the Urdu name (or the name). If taken, add the parent's name, then the type, then a number.
        The database ignores diacritics such as zer, so 'فنِ تعمیر' and 'فن تعمیر' count as the same ID."""
        base = clean_name(self.category_name_ur or self.category_name)
        if not base:
            frappe.throw("Category name is required")
        candidates = [base]
        if self.parent_book_category:
            parent = frappe.db.get_value("Book Category", self.parent_book_category,
                                         ["category_name_ur", "category_name"], as_dict=True) or {}
            plabel = clean_name(parent.get("category_name_ur") or parent.get("category_name"))
            if plabel:
                candidates.append(f"{base} ({plabel})")
        candidates.append(f"{candidates[-1]} - {TYPE_UR.get(self.category_type, self.category_type or '')}".strip(" -"))
        for c in candidates:
            if not frappe.db.exists("Book Category", c):
                self.name = c
                return
        n = 2
        while frappe.db.exists("Book Category", f"{candidates[-1]} {n}"):
            n += 1
        self.name = f"{candidates[-1]} {n}"
