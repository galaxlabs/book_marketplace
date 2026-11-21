"""Ordering a chosen option of an item (size, colour, style, amount) and its own stock.

An item's options live on the item (Book.item_options) and belong to the shop that added it (or its publisher).
Only that shop sells by option: its option stock goes down when an order takes it and comes back when the order
is cancelled or expires. The shop's total stock (StockLedger) moves with it, as before.
Another shop that bought the item holds it as one stock, without options.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt


def label_of(o):
    amount = f"{flt(o.amount):g} {o.unit or ''}".strip() if flt(o.amount) else ""
    return " · ".join(x for x in (o.size, o.colour, o.style, amount) if x) or "—"


def owner_of(book):
    b = frappe.db.get_value("Book", book, ["publisher", "owner_profile"], as_dict=True) or {}
    return b.get("owner_profile") or b.get("publisher")


def has_options(book):
    return bool(frappe.db.count("Item Option", {"parent": book, "parenttype": "Book"}))


def options_of(book):
    rows = frappe.get_all("Item Option", filters={"parent": book, "parenttype": "Book"},
                          fields=["name", "size", "colour", "style", "amount", "unit", "price", "stock_qty"],
                          order_by="idx asc", limit_page_length=0)
    # size / colour / style / amount apart, so a store page can show one row of choices per attribute
    return [{"id": r.name, "label": label_of(r), "price": flt(r.price), "stock": cint(r.stock_qty),
             "size": r.size or "", "colour": r.colour or "", "style": r.style or "",
             "amount": (f"{flt(r.amount):g} {r.unit or ''}".strip() if flt(r.amount) else "")} for r in rows]


def get_option(book, option_id):
    row = frappe.db.get_value("Item Option", {"name": option_id, "parent": book, "parenttype": "Book"},
                              ["name", "size", "colour", "style", "amount", "unit", "price", "stock_qty"], as_dict=True)
    if not row:
        frappe.throw(_("This option is not offered for {0}.").format(frappe.db.get_value("Book", book, "title") or book))
    return row


def take(book, option_id, qty):
    """Take qty from the option's stock (locked, so two orders cannot take the same pieces)."""
    qty = cint(qty)
    if qty <= 0:
        return
    cur = frappe.db.sql("select stock_qty from `tabItem Option` where name=%s and parent=%s for update", (option_id, book))
    if not cur:
        frappe.throw(_("This option is no longer offered."))
    have = cint(cur[0][0])
    if have < qty:
        row = get_option(book, option_id)
        frappe.throw(_("Only {0} left of {1}.").format(have, label_of(row)))
    frappe.db.sql("update `tabItem Option` set stock_qty = stock_qty - %s where name=%s", (qty, option_id))


def give_back(book, option_id, qty):
    qty = cint(qty)
    if qty > 0 and option_id:
        frappe.db.sql("update `tabItem Option` set stock_qty = stock_qty + %s where name=%s and parent=%s", (qty, option_id, book))


def take_shop_stock(profile_id, book, qty):
    """The seller's total Available stock goes down with the option (never below zero)."""
    led = frappe.db.get_value("StockLedger", {"business_profile": profile_id, "book": book, "state": "Available"}, "name")
    if led:
        frappe.db.sql("update `tabStockLedger` set quantity = greatest(quantity - %s, 0) where name=%s", (cint(qty), led))


@frappe.whitelist(allow_guest=True)
def item_options(book):
    """The options of an item with their price and stock, for the order form and the website."""
    return {"book": book, "owner": owner_of(book), "options": options_of(book)}
