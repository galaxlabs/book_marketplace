import frappe
from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class StockTransfer(Document):
    def autoname(self):
        self.name = monthly_name("TRF")

    def on_submit(self):
        self.update_stock_ledger()

    def update_stock_ledger(self):
        pass
