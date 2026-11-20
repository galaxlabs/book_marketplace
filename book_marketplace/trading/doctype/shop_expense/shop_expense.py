from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class ShopExpense(Document):
    def autoname(self):
        self.name = monthly_name("EXP")

    pass
