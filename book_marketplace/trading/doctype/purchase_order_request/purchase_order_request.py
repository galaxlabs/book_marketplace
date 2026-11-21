from frappe.model.document import Document

from book_marketplace.trading.series import monthly_name


class PurchaseOrderRequest(Document):
    def autoname(self):
        self.name = monthly_name("PO")

    def before_insert(self):
        # the buyer's own number for its order, and both sides keep the other in their saved list
        from book_marketplace.trading import doc_numbers
        if not self.buyer_doc_no:
            self.buyer_doc_no = doc_numbers.next_number(self.buyer_profile, "PO", self.seller_profile)
        doc_numbers.remember_party(self.buyer_profile, self.seller_profile, "Seller")
        doc_numbers.remember_party(self.seller_profile, self.buyer_profile, "Buyer")

    pass
