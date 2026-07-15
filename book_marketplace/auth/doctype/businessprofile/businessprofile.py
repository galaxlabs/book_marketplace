import frappe
from frappe.model.document import Document


class BusinessProfile(Document):
    def before_save(self):
        if self.status == "Approved" and not self.approved_on:
            self.approved_on = frappe.utils.today()
