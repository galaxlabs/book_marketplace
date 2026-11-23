import frappe
from frappe.model.document import Document


class ReaderListing(Document):
    def validate(self):
        if self.kind == "For sale" and not (self.price or 0) > 0:
            frappe.throw("Price is needed for a sale.")
        if self.kind == "Gift":
            self.price = 0
