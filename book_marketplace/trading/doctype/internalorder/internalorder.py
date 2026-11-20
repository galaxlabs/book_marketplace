import frappe
from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class InternalOrder(Document):
    def autoname(self):
        self.name = monthly_name("ORD")

    def before_save(self):
        self.calculate_total()

    def calculate_total(self):
        total = 0
        for item in self.get("items", []):
            total += item.amount or (item.quantity * item.rate)
        self.total_amount = total
