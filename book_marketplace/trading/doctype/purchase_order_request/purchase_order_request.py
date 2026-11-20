from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class PurchaseOrderRequest(Document):
    def autoname(self):
        self.name = monthly_name("PO")

    pass
