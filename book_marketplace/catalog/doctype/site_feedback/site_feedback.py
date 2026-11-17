import frappe
from frappe.model.document import Document


class SiteFeedback(Document):
    def validate(self):
        self.message = (self.message or "").strip()
        if len(self.message) < 5:
            frappe.throw("Please write at least 5 characters. / کم از کم 5 حروف لکھیں۔")
