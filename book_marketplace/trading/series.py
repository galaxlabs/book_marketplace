"""Document numbers for transaction documents.

Format: PREFIX-YYYY-MM-DD-NNNN, for example PO-2026-11-20-0001.
- The year, month and day show when the document was made.
- NNNN is a counter that starts again at 0001 on the 1st of every month.
- The counter comes from Frappe's own series table, so two users saving at the same time get different numbers.
"""
import frappe
from frappe.model.naming import make_autoname
from frappe.utils import getdate


def monthly_name(prefix):
    today = getdate()
    # Frappe keeps one counter per expanded key, so "PO-2026-11-" has its own counter that restarts each month.
    seq = make_autoname(f"{prefix}-.YYYY.-.MM.-.####").rsplit("-", 1)[1]
    return f"{prefix}-{today:%Y-%m-%d}-{seq}"
