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


def on_book_update(doc, method=None):
    before = doc.get_doc_before_save()
    if not before:
        return
    old, new = frappe.utils.flt(before.set_mrp), frappe.utils.flt(doc.set_mrp)
    if old == new:
        return
    publisher = doc.get("publisher")
    holders = frappe.get_all("BusinessCatalogItem", filters={"book": doc.name, "is_active": 1},
                             fields=["business_profile"], limit_page_length=0)
    from book_marketplace.trading.api.notifications import notify
    title = doc.title or doc.name
    now = now_datetime()
    shops = 0
    for h in holders:
        if not h.business_profile or h.business_profile == publisher:
            continue
        frappe.get_doc({"doctype": "Catalog Price Change", "business_profile": h.business_profile, "book": doc.name,
                        "publisher": publisher, "old_price": old, "new_price": new, "changed_on": now,
                        "seen": 0}).insert(ignore_permissions=True)
        notify(h.business_profile, _("Price changed: {0}").format(title),
               _("The publisher's price is now {0} (was {1}). Your catalogue shows the new price.").format(new, old),
               link="/catalog", kind="price")
        shops += 1
    if shops and publisher:
        frappe.get_doc({"doctype": "Shop Board Post", "business_profile": publisher, "status": "Open",
                        "body": _("Price update: «{0}» is now {1} (was {2}). Shops that list this book see the new price.").format(title, new, old),
                        "reports": 0, "created_on": now}).insert(ignore_permissions=True)


@frappe.whitelist()
def price_change_status(profile_id):
    """Books whose price changed and that the shop has not opened the catalogue since (highlighted)."""
    _assert_profile_owner(profile_id)
    return [r.book for r in frappe.get_all("Catalog Price Change",
                                           filters={"business_profile": profile_id, "seen": 0},
                                           fields=["book"], limit_page_length=0, distinct=1)]


@frappe.whitelist()
def mark_price_changes_seen(profile_id):
    _assert_profile_owner(profile_id)
    frappe.db.sql("update `tabCatalog Price Change` set seen = 1 where business_profile = %s and seen = 0", (profile_id,))
    return {"status": "seen"}
