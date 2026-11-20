from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class KhataEntry(Document):
    def autoname(self):
        self.name = monthly_name("KHATA")

    def validate(self):
        if self.amount is not None and float(self.amount) <= 0:
            import frappe
            frappe.throw("Amount must be greater than zero.")
