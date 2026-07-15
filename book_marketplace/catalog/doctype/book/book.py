import frappe
from frappe.model.document import Document


class Book(Document):
    def before_save(self):
        self.detect_duplicates()
        self.validate_volumes()

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
