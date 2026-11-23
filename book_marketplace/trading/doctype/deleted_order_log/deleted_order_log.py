import frappe
from frappe.model.document import Document


class DeletedOrderLog(Document):
    """A record kept when a customer order is deleted. Only orders not yet accepted by the shop can be deleted."""

    pass
