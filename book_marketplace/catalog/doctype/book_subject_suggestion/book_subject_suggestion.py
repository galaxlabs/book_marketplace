import frappe
from frappe.model.document import Document


class BookSubjectSuggestion(Document):
    """A subject suggested for a book by a reader, a shop or the publisher.
    The publisher's suggestion, or the same subject from two different people, is applied at once
    (see catalog/api/subject_suggestion.py); the rest wait for review."""
    pass
