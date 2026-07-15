import frappe
from frappe.model.document import Document


class InternalOrder(Document):
    def before_save(self):
        self.calculate_total()

    def calculate_total(self):
        total = 0
        for item in self.get("items", []):
            total += item.amount or (item.quantity * item.rate)
        self.total_amount = total
