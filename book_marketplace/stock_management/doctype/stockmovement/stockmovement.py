from frappe.model.document import Document


class StockMovement(Document):
    """One line of the stock history: a change of one StockLedger row (a quantity going up or down) with the reason.
    Lines are written by book_marketplace.stock_management.movement only; nobody edits them."""
    pass
