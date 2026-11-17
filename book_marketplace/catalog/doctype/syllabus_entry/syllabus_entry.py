import frappe
from frappe.model.document import Document


class SyllabusEntry(Document):
    def validate(self):
        self.book_title = (self.book_title or "").strip()
        if self.darja and not self.board:
            frappe.throw("Board is required.")
