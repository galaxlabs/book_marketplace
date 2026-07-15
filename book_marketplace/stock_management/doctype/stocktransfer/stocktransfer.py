import frappe
from frappe.model.document import Document


class StockTransfer(Document):
    def on_submit(self):
        self.update_stock_ledger()

    def update_stock_ledger(self):
        pass
