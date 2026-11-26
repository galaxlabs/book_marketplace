"""Customer portal extras (dev-b): follows (favourites with alerts), notifications, and the shop's order bill.

Follow types: Book (alert when back in stock), Author / Category / Publisher / Shop (alert when a new book is listed).
No prices, percentages or discount chains are returned to buyers here."""
import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from book_marketplace.trading.api.customer_order import _buyer_from_session

FOLLOW_TYPES = ("Book", "Author", "Category", "Publisher", "Shop", "User", "Subject")


def _label(ftype, target):
    if ftype == "Book":
        return frappe.db.get_value("Book", target, "title")
    if ftype == "Category":
        row = frappe.db.get_value("Book Category", target, ["category_name_ur", "category_name"], as_dict=True)
        return (row.category_name_ur or row.category_name) if row else None
    if ftype in ("Publisher", "Shop"):
        return frappe.db.get_value("BusinessProfile", target, "business_name")
    if ftype == "User":
        return frappe.db.get_value("BusinessProfile", {"user": target}, "business_name") or target
    if ftype == "Subject":
        return frappe.db.get_value("SubjectMaster", target, "subject_name") or target
    return target  # Author: the author's name as written on the book


def _target_exists(ftype, target):
    if ftype == "Book":
        return frappe.db.exists("Book", target)
    if ftype == "Category":
        return frappe.db.exists("Book Category", target)
    if ftype in ("Publisher", "Shop"):
        return frappe.db.exists("BusinessProfile", target)
    if ftype == "User":
        return frappe.db.exists("User", target) and target != frappe.session.user
    if ftype == "Subject":
        return frappe.db.exists("SubjectMaster", target)
    return bool(target)


@frappe.whitelist()
def follow(follow_type, target, notify=1):
    """Add to the buyer's list (پسندیدہ). notify=1 turns on alerts for it."""
    buyer = _buyer_from_session()
    if follow_type not in FOLLOW_TYPES:
        frappe.throw(_("Unknown kind."))
    target = (target or "").strip()
    if not target or not _target_exists(follow_type, target):
        frappe.throw(_("Not found."))
    row = frappe.db.get_value("Customer Follow", {"buyer_profile": buyer, "follow_type": follow_type, "target": target},
                              "name")
    if row:
        frappe.db.set_value("Customer Follow", row, "notify", 1 if int(notify) else 0)
        name = row
    else:
        name = frappe.get_doc({
            "doctype": "Customer Follow", "buyer_profile": buyer, "follow_type": follow_type, "target": target,
            "target_label": _label(follow_type, target), "notify": 1 if int(notify) else 0, "added_on": now_datetime(),
        }).insert(ignore_permissions=True).name
    return {"name": name, "follow_type": follow_type, "target": target, "notify": 1 if int(notify) else 0}


@frappe.whitelist()
def unfollow(follow_type, target):
    buyer = _buyer_from_session()
    row = frappe.db.get_value("Customer Follow", {"buyer_profile": buyer, "follow_type": follow_type, "target": target},
                              "name")
    if row:
        frappe.delete_doc("Customer Follow", row, ignore_permissions=True, force=True)
    return {"ok": True}


@frappe.whitelist()
def set_follow_notify(name, notify):
    buyer = _buyer_from_session()
    if frappe.db.get_value("Customer Follow", name, "buyer_profile") != buyer:
        frappe.throw(_("Not found."), frappe.PermissionError)
    frappe.db.set_value("Customer Follow", name, "notify", 1 if int(notify) else 0)
    return {"name": name, "notify": 1 if int(notify) else 0}


@frappe.whitelist()
def my_follows():
    """The buyer's list, grouped by the caller. Empty when not logged in as a buyer."""
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if not buyer:
        return []
    return frappe.get_all("Customer Follow", filters={"buyer_profile": buyer},
                          fields=["name", "follow_type", "target", "target_label", "notify", "added_on"],
                          order_by="added_on desc", limit_page_length=500)


@frappe.whitelist()
def follow_state(follow_type, target):
    """Is this book/shop/author in the buyer's list? Used by cards to show the right button."""
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if not buyer:
        return {"followed": False, "notify": False}
    row = frappe.db.get_value("Customer Follow", {"buyer_profile": buyer, "follow_type": follow_type, "target": target},
                              ["name", "notify"], as_dict=True)
    return {"followed": bool(row), "notify": bool(row and row.notify), "name": row.name if row else None}


@frappe.whitelist()
def my_notifications(limit=50):
    buyer = _buyer_from_session()
    rows = frappe.get_all("Customer Notification", filters={"buyer_profile": buyer},
                          fields=["name", "kind", "message", "book", "seller_profile", "is_read", "created_on"],
                          order_by="created_on desc", limit_page_length=int(limit))
    for r in rows:
        r["business_name"] = frappe.db.get_value("BusinessProfile", r.seller_profile, "business_name") if r.seller_profile else None
        r.pop("seller_profile", None)
    return rows


@frappe.whitelist()
def unread_notifications():
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if not buyer:
        return {"count": 0}
    return {"count": frappe.db.count("Customer Notification", {"buyer_profile": buyer, "is_read": 0})}


@frappe.whitelist()
def mark_notifications_read():
    buyer = _buyer_from_session()
    frappe.db.sql("update `tabCustomer Notification` set is_read=1 where buyer_profile=%s and is_read=0", buyer)
    return {"ok": True}


# ---------- alerts: called by the stock and catalog code ----------

def _ancestors(category):
    out, cur = [], category
    while cur and len(out) < 20:
        out.append(cur)
        cur = frappe.db.get_value("Book Category", cur, "parent_book_category")
    return out


def _book_context(book):
    bk = frappe.db.get_value("Book", book, ["title", "author", "publisher"], as_dict=True)
    cats = set()
    for c in frappe.get_all("BookClassification", filters={"parent": book, "parenttype": "Book"}, pluck="category"):
        cats.update(_ancestors(c))
    return bk, cats


def _subject_chain(book):
    """The book's subjects and every parent subject above them (a topic page includes its sub-topics)."""
    seeds = frappe.get_all("BookSubject", filters={"parent": book, "parenttype": "Book"}, pluck="subject")
    primary = frappe.db.get_value("Book", book, "primary_category")
    if primary:
        seeds.append(primary)
    out, seen = [], set()
    for s_ in seeds:
        cur = s_
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = frappe.db.get_value("SubjectMaster", cur, "parent_subject")
    return out


def _followers(book, seller, kinds):
    """Buyers to tell. kinds: 'Back in stock' (Book follows) or 'New book' (Author, Category, Publisher, Shop follows)."""
    bk, cats = _book_context(book)
    if not bk:
        return {}
    wanted = []
    if kinds == "Back in stock":
        wanted.append(("Book", book))
    else:
        if bk.author:
            wanted.append(("Author", bk.author))
        if bk.publisher:
            wanted.append(("Publisher", bk.publisher))
        if seller:
            wanted.append(("Shop", seller))
        wanted += [("Category", c) for c in cats]
        wanted += [("Subject", x) for x in _subject_chain(book)]
    out = {}
    for ftype, target in wanted:
        for r in frappe.get_all("Customer Follow", filters={"follow_type": ftype, "target": target, "notify": 1},
                                fields=["buyer_profile"]):
            out.setdefault(r.buyer_profile, ftype)
    if kinds != "Back in stock" and bk.publisher:
        pub_user = frappe.db.get_value("BusinessProfile", bk.publisher, "user")
        pub_buyer = frappe.db.get_value("BuyerProfile", {"user": pub_user}, "name") if pub_user else None
        if pub_buyer:
            out.pop(pub_buyer, None)
    return out


def _send(buyers, kind, message, book, seller):
    for buyer in buyers:
        if kind == "New book" and frappe.db.exists("Customer Notification", {"buyer_profile": buyer, "kind": kind, "book": book}):
            continue
        frappe.get_doc({"doctype": "Customer Notification", "buyer_profile": buyer, "kind": kind, "message": message,
                        "book": book, "seller_profile": seller, "is_read": 0, "created_on": now_datetime()}
                       ).insert(ignore_permissions=True)


def notify_back_in_stock(seller, book, before, after):
    """A shop's copies went from none to some: tell buyers who follow this book."""
    if flt(before) > 0 or flt(after) <= 0:
        return
    buyers = _followers(book, seller, "Back in stock")
    if not buyers:
        return
    title = frappe.db.get_value("Book", book, "title") or book
    shop = frappe.db.get_value("BusinessProfile", seller, "business_name") or ""
    _send(list(buyers), "Back in stock", f"{title} اسٹاک میں آ گئی ہے ({shop})", book, seller)


def on_catalog_item_added(doc, method=None):
    """A shop listed a book: tell buyers who follow its author, publisher, shop or category, if copies are there."""
    if not doc.is_active or not doc.book:
        return
    buyers = _followers(doc.book, doc.business_profile, "New book")
    if not buyers:
        return
    title = frappe.db.get_value("Book", doc.book, "title") or doc.book
    shop = frappe.db.get_value("BusinessProfile", doc.business_profile, "business_name") or ""
    _send(list(buyers), "New book", f"نئی کتاب: {title} ({shop})", doc.book, doc.business_profile)


@frappe.whitelist()
def seller_order_bill(order):
    """For the shop's billing screen: the order's lines as a bill (scan its QR). Shop-only; no buyer's contact here."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    doc = frappe.get_doc("Customer Order", order)
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(doc.seller_profile, "orders.view")
    buyer_name = frappe.db.get_value("BuyerProfile", doc.buyer_profile, "full_name")
    items = [{"book": i.book, "title": frappe.db.get_value("Book", i.book, "title"), "qty": int(i.qty),
              "unit_price": flt(i.unit_price), "line_total": flt(i.line_total)} for i in doc.items]
    return {"name": doc.name, "status": doc.status, "payment_status": doc.payment_status, "buyer_name": buyer_name,
            "subtotal": flt(doc.subtotal), "total": flt(doc.total), "items": items}
