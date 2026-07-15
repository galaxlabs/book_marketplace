import frappe
from frappe.model.document import Document


class InternalOrderItem(Document):
    def before_save(self):
        self.amount = self.quantity * self.rate
