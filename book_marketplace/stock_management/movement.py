"""The stock history. Every change of a StockLedger quantity goes through here, so each one leaves a StockMovement line:
when, where, which book, how much (+ or -), before and after, why, which order or purchase, and who did it.

direction: In (goods came), Out (goods left), Internal (the same goods changed state, e.g. reserved for an order),
Opening (the quantity that was already there when the history began)."""
import frappe
from frappe.utils import cint, now_datetime

# reason key -> (text shown, direction; None = In when the quantity rose, Out when it fell)
REASONS = {
    "opening": ("Opening balance", "Opening"),
    "received": ("Received (purchase order)", "In"),
    "sold": ("Sold", "Out"),
    "delivered": ("Delivered to the customer", "Out"),
    "reserved": ("Reserved for an order", "Internal"),
    "released": ("Order cancelled, copies back", "Internal"),
    "manual": ("Quantity set by hand", None),
    "transfer_in": ("Transfer in", "In"),
    "transfer_out": ("Transfer out", "Out"),
}


def log(ledger, before, after, reason, ref_type=None, ref_name=None):
    """Write the history line of one ledger row going from `before` to `after`. Nothing is written when nothing changed."""
    before, after = cint(before), cint(after)
    if before == after:
        return None
    row = frappe.db.get_value("StockLedger", ledger, ["business_profile", "branch_location", "book", "volume", "state"], as_dict=True)
    if not row:
        return None
    text, direction = REASONS[reason]
    if direction is None:
        direction = "In" if after > before else "Out"
    line = frappe.get_doc({
        "doctype": "StockMovement", "occurred_on": now_datetime(), "business_profile": row.business_profile,
        "branch_location": row.branch_location, "book": row.book, "volume": row.volume or "", "state": row.state,
        "change": after - before, "qty_before": before, "qty_after": after, "direction": direction, "reason": text,
        "reference_type": ref_type or "", "reference_name": ref_name or "", "user": frappe.session.user, "ledger": ledger,
    })
    line.insert(ignore_permissions=True)
    return line.name


def set_qty(ledger, new_qty, reason, ref_type=None, ref_name=None):
    """Put `new_qty` on a ledger row and log the change. Returns (before, after)."""
    cur = frappe.db.sql("select quantity from `tabStockLedger` where name=%s for update", ledger)
    before = cint(cur[0][0]) if cur else 0
    after = max(0, cint(new_qty))
    frappe.db.set_value("StockLedger", ledger, "quantity", after)
    log(ledger, before, after, reason, ref_type, ref_name)
    return before, after


def change_qty(ledger, delta, reason, ref_type=None, ref_name=None):
    """Add `delta` (negative to take out) to a ledger row, never below zero, and log it. Returns (before, after)."""
    cur = frappe.db.sql("select quantity from `tabStockLedger` where name=%s for update", ledger)
    before = cint(cur[0][0]) if cur else 0
    return set_qty(ledger, before + cint(delta), reason, ref_type, ref_name)


def add_row(book, business_profile, branch_location, quantity, state, reason, ref_type=None, ref_name=None, volume=None):
    """A new ledger row (nothing of this book was there in this state) and its history line. Returns the row name."""
    doc = frappe.get_doc({"doctype": "StockLedger", "book": book, "business_profile": business_profile,
                          "branch_location": branch_location, "quantity": max(0, cint(quantity)), "state": state,
                          **({"volume": volume} if volume else {})})
    doc.insert(ignore_permissions=True)
    log(doc.name, 0, doc.quantity, reason, ref_type, ref_name)
    return doc.name
