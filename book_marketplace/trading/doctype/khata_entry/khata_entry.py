from frappe.model.document import Document


class KhataEntry(Document):
    def validate(self):
        if self.amount is not None and float(self.amount) <= 0:
            import frappe
            frappe.throw("Amount must be greater than zero.")
