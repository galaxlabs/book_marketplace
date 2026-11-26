"""When a publisher changes the price of a book, every shop that holds the book is told.

- Catalog Price Change: one row per shop and change, so a shop can see the changed prices highlighted until it opens
  its catalogue.
- Shop Notification: a short note in each shop's panel.
- Message board: one notice from the publisher. It is written as "Open" directly, because the board hides posts that
  talk about prices.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

from book_marketplace.auth.api.auth import _assert_profile_owner


def _discount_of(book):
    from book_marketplace.catalog.api.catalog import publisher_discount

    return publisher_discount(book=book)


def on_book_update(doc, method=None):
    """The publisher changed the book's price, trade price or discount: every shop holding the book is told."""
    before = doc.get_doc_before_save()
    if not before:
        return
    old_mrp, new_mrp = frappe.utils.flt(before.set_mrp), frappe.utils.flt(doc.set_mrp)
    old_trade, new_trade = frappe.utils.flt(before.get("trade_price")), frappe.utils.flt(doc.get("trade_price"))
    old_disc, new_disc = _discount_of(before.as_dict()), _discount_of(doc.as_dict())
    changes = []
    if old_mrp != new_mrp:
        changes.append(_("price {0} (was {1})").format(new_mrp, old_mrp))
    if old_disc != new_disc:
        changes.append(_("discount {0}% (was {1}%)").format(new_disc, old_disc))
    if old_trade != new_trade:
        changes.append(_("trade price {0} (was {1})").format(new_trade, old_trade))
    if not changes:
        return
    _tell_holders([doc.name], doc.get("publisher"), doc.title or doc.name, "; ".join(changes),
                  old_mrp, new_mrp)


def _tell_holders(books, publisher, title, what, old_price=0, new_price=0):
    """One price-change row per shop and book (highlighted in its catalogue) and one note per shop."""
    holders = frappe.get_all("Business Catalog Item", filters={"book": ["in", books], "is_active": 1},
                             fields=["business_profile", "book"], limit_page_length=0)
    from book_marketplace.trading.api.notifications import notify
    now = now_datetime()
    told = set()
    for h in holders:
        if not h.business_profile or h.business_profile == publisher:
            continue
        frappe.get_doc({"doctype": "Catalog Price Change", "business_profile": h.business_profile, "book": h.book,
                        "publisher": publisher, "old_price": old_price, "new_price": new_price, "changed_on": now,
                        "seen": 0}).insert(ignore_permissions=True)
        if h.business_profile in told:
            continue
        told.add(h.business_profile)
        notify(h.business_profile, _("Price changed: {0}").format(title),
               _("The publisher changed {0}. Your catalogue shows the new prices.").format(what),
               link="/catalog", kind="price")
    return len(told)


def on_general_discount_change(publisher, old_disc, new_disc):
    """The publisher's general discount changed: shops holding its books that use the general rate are told."""
    if frappe.utils.flt(old_disc) == frappe.utils.flt(new_disc):
        return 0
    books = frappe.get_all("Book", filters={"publisher": publisher, "own_discount": 0}, pluck="name",
                           limit_page_length=0)
    if not books:
        return 0
    name = frappe.db.get_value("BusinessProfile", publisher, "business_name") or publisher
    return _tell_holders(books, publisher, name,
                         _("its general discount to {0}% (was {1}%)").format(new_disc, old_disc))


@frappe.whitelist()
def price_change_status(profile_id):
    """Books whose price changed and that the shop has not opened the catalogue since (highlighted)."""
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.view")
    return [r.book for r in frappe.get_all("Catalog Price Change",
                                           filters={"business_profile": profile_id, "seen": 0},
                                           fields=["book"], limit_page_length=0, distinct=1)]


@frappe.whitelist()
def mark_price_changes_seen(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.view")
    frappe.db.sql("update `tabCatalog Price Change` set seen = 1 where business_profile = %s and seen = 0", (profile_id,))
    return {"status": "seen"}
