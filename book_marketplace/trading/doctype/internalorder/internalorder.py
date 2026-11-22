import frappe
from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class InternalOrder(Document):
    def autoname(self):
        self.name = monthly_name("ORD")

    def before_save(self):
        self.calculate_total()

    def calculate_total(self):
        subtotal = 0
        for item in self.get("items", []):
            subtotal += item.amount or (item.quantity * item.rate)
        self.subtotal = subtotal
        # the shop's own discount on this bill: a percent, or an amount (not more than the bill)
        pct = float(self.get("discount_percent") or 0)
        amount = float(self.get("discount_amount") or 0)
        discount = round(subtotal * pct / 100, 2) if pct else amount
        discount = min(max(discount, 0), subtotal)
        self.discount_amount = discount
        self.total_amount = subtotal - discount
