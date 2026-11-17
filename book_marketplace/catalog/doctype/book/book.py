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
        self.detect_duplicates()
        self.validate_volumes()

    def after_save(self):
        self.generate_qr_code()

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
