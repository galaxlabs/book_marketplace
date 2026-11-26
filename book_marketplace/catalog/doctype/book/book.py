import frappe
import json
from frappe.model.document import Document
from frappe.utils import get_url


class Book(Document):
    def autoname(self):
        """Title is the name while it is free. A second book with the same title (different author
        or edition) gets 'title - author', then 'title - author - publisher', then a counter.
        Existing books keep their names."""
        base = (self.title or "").strip()
        if not base:
            frappe.throw("Title is required")
        if not frappe.db.exists("Book", base):
            self.name = base
            return
        author = (self.author or "").strip()
        candidate = f"{base} - {author}" if author else base
        if not frappe.db.exists("Book", candidate):
            self.name = candidate
            return
        if self.publisher:
            candidate = f"{candidate} - {self.publisher}"
            if not frappe.db.exists("Book", candidate):
                self.name = candidate
                return
        n = 2
        while frappe.db.exists("Book", f"{candidate} ({n})"):
            n += 1
        self.name = f"{candidate} ({n})"

    def before_save(self):
        self.sync_primary_language()
        self.detect_duplicates()
        self.validate_volumes()
        self.validate_textbook()
        self.validate_sharh_of()

    def after_save(self):
        self.generate_qr_code()

    def sync_primary_language(self):
        rows = [r.language for r in (self.get("languages") or []) if r.language]
        if len(set(rows)) > 1:
            frappe.throw("Each language is a separate product (one product per language). Add it as another book.")
        if rows:
            self.language = rows[0]

    def detect_duplicates(self):
        filters = {"name": ["!=", self.name]} if self.name else {}
        if self.isbn:
            filters["isbn"] = self.isbn
            existing = frappe.db.get_value("Book", filters, "name")
            if existing:
                frappe.msgprint(f"Warning: ISBN already exists for Book <b>{existing}</b>")
                return
        if self.title and self.author and self.publisher:
            filters = {
                "title": self.title,
                "author": self.author,
                "publisher": self.publisher,
                "name": ["!=", self.name or ""],
            }
            existing = frappe.db.get_value("Book", filters, "name")
            if existing:
                frappe.msgprint(f"Warning: Similar book exists: <b>{existing}</b>")

    def validate_sharh_of(self):
        if self.sharh_of and self.sharh_of == self.name:
            frappe.throw("ایک کتاب اپنی ہی شرح نہیں ہو سکتی۔")
        if self.sharh_of and frappe.db.get_value("Book", self.sharh_of, "sharh_of") == self.name:
            frappe.throw("دو کتابیں ایک دوسرے کی شرح نہیں ہو سکتیں۔")

    def validate_textbook(self):
        """Textbook rules: boards and Dars-e-Nizami darja. Wifaq implies Dars-e-Nizami.
        Board and darja nodes are also written to the classification table, so department search finds the book."""
        if not self.is_textbook:
            self.set("exam_boards", [])
            self.is_dars_e_nizami = 0
            self.darja = None
        boards = [r.board for r in (self.get("exam_boards") or []) if r.board]
        if self.is_textbook and any(self._is_wifaq(b) for b in boards):
            self.is_dars_e_nizami = 1
        if not self.is_dars_e_nizami:
            self.darja = None
        if self.is_dars_e_nizami and not self.darja:
            frappe.throw("درجہ لازمی ہے: درسِ نظامی کی کتاب کے لیے درجہ منتخب کریں۔")
        if self.darja and frappe.db.get_value("Book Category", self.darja, "category_type") != "Department":
            frappe.throw("درجہ صرف شعبہ جات (Department) میں سے ہو سکتا ہے۔")
        self.sync_textbook_classifications(boards)

    @staticmethod
    def _is_wifaq(board):
        label = frappe.db.get_value("Book Category", board, ["category_name", "category_name_ur"], as_dict=True) or {}
        return "وفاق" in f"{label.get('category_name') or ''} {label.get('category_name_ur') or ''}"

    def sync_textbook_classifications(self, boards):
        old = self.get_doc_before_save()
        old_managed = set()
        if old:
            old_managed = {r.board for r in (old.get("exam_boards") or []) if r.board}
            if old.darja:
                old_managed.add(old.darja)
        new_managed = set(boards)
        if self.darja:
            new_managed.add(self.darja)
        kept = [r for r in (self.get("classifications") or [])
                if not (r.category in old_managed and r.category not in new_managed)]
        self.set("classifications", kept)
        present = {r.category for r in kept}
        for cat in sorted(new_managed - present):
            self.append("classifications", {"category": cat})

    def validate_volumes(self):
        if self.book_type != "Multi Volume":
            self.set("volumes", [])
            return
        volumes = self.get("volumes", [])
        if not volumes:
            frappe.throw("Multi-volume books must have at least one volume entry")

    def generate_qr_code(self):
        if self.qr_code:
            return
        try:
            url = get_url(f"/books/{self.name}")
            from frappe.utils import get_files_path
            import os, qrcode
            from io import BytesIO
            img = qrcode.make(url)
            file_name = f"qr_{self.name}.png"
            file_path = get_files_path(file_name, is_private=0)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            img.save(file_path)
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": file_name,
                "file_url": f"/files/{file_name}",
                "attached_to_doctype": "Book",
                "attached_to_name": self.name,
                "is_private": 0,
            })
            file_doc.insert(ignore_permissions=True)
            self.db_set("qr_code", file_doc.file_url)
        except ImportError:
            pass
