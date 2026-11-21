import frappe
from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class CustomerOrder(Document):
    def autoname(self):
        self.name = monthly_name("CO")

    def before_insert(self):
        # the shop's own number for a website order, with the buyer's initials
        from book_marketplace.trading import doc_numbers
        if not self.seller_doc_no and self.seller_profile:
            who = self.ship_name or frappe.db.get_value("BuyerProfile", self.buyer_profile, "full_name") or ""
            self.seller_doc_no = doc_numbers.next_number(self.seller_profile, "CO", None, who)

    pass
