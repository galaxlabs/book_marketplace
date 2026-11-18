"""Messages between accounts, saved contacts, and book questions sent only to shops that hold the book.

Any logged-in account can talk to any other account (buyers, shops, publishers, distributors, agents).
The browser polls `messages(after=...)` every few seconds; no websocket is needed for this version.
"""
import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

MAX_BODY = 2000


def _me():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return user


def _display(user):
    name = frappe.db.get_value("BusinessProfile", {"user": user}, "business_name")
    return name or frappe.db.get_value("User", user, "full_name") or user


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _conversation_for(a, b):
    x, y = _pair(a, b)
    name = frappe.db.get_value("Chat Conversation", {"user_a": x, "user_b": y}, "name")
    if name:
        return name
    doc = frappe.get_doc({"doctype": "Chat Conversation", "user_a": x, "user_b": y}).insert(ignore_permissions=True)
    return doc.name


def _member(conversation, user):
    conv = frappe.db.get_value("Chat Conversation", conversation, ["user_a", "user_b"], as_dict=True)
    if not conv or user not in (conv.user_a, conv.user_b):
        frappe.throw(_("This conversation is not yours."), frappe.PermissionError)
    return conv


def _body(text):
    text = (text or "").strip()
    if not text:
        frappe.throw(_("Write a message first."))
    if len(text) > MAX_BODY:
        frappe.throw(_("Message is too long (max {0} characters).").format(MAX_BODY))
    return text


def _read_field(conversation, user):
    """Each side has its own 'read up to' time: read_a for user_a, read_b for user_b."""
    conv = frappe.db.get_value("Chat Conversation", conversation, ["user_a", "user_b"], as_dict=True)
    return "read_a" if conv.user_a == user else "read_b"


def _post(conversation, sender, body, book=None):
    msg = frappe.get_doc({
        "doctype": "Chat Message", "conversation": conversation, "sender": sender,
        "body": body, "book": book or None,
    }).insert(ignore_permissions=True)
    now = now_datetime()
    frappe.db.set_value("Chat Conversation", conversation,
                        {"last_message_at": now, "last_preview": body[:140], _read_field(conversation, sender): now},
                        update_modified=True)
    return {"name": msg.name, "sender": sender, "body": body, "book": book or None,
            "creation": str(msg.creation)}


@frappe.whitelist()
def start_conversation(user):
    me = _me()
    if not user or user == me:
        frappe.throw(_("Choose another account."))
    if not frappe.db.exists("User", user):
        frappe.throw(_("Account not found."))
    conv = _conversation_for(me, user)
    return {"name": conv, "other": user, "other_name": _display(user)}


@frappe.whitelist()
def my_conversations(limit=50):
    me = _me()
    rows = frappe.get_all(
        "Chat Conversation",
        or_filters=[["user_a", "=", me], ["user_b", "=", me]],
        fields=["name", "user_a", "user_b", "last_message_at", "last_preview", "read_a", "read_b"],
        order_by="modified desc",
        limit_page_length=int(limit),
    )
    out = []
    for r in rows:
        other = r.user_b if r.user_a == me else r.user_a
        my_read = r.read_a if r.user_a == me else r.read_b
        unread = bool(r.last_message_at) and (not my_read or get_datetime(r.last_message_at) > get_datetime(my_read))
        out.append({"name": r.name, "other": other, "other_name": _display(other),
                    "last_message_at": str(r.last_message_at or ""), "last_preview": r.last_preview or "",
                    "unread": bool(unread)})
    return out


@frappe.whitelist()
def mark_read(conversation):
    """The viewer opened the conversation: everything up to now counts as read (seen)."""
    me = _me()
    _member(conversation, me)
    frappe.db.set_value("Chat Conversation", conversation, _read_field(conversation, me), now_datetime(),
                        update_modified=False)
    return {"ok": True}


@frappe.whitelist()
def receipts(conversation):
    """When the other side last opened this conversation (drives the 'read' tick)."""
    me = _me()
    conv = _member(conversation, me)
    other_field = "read_b" if conv.user_a == me else "read_a"
    other_read = frappe.db.get_value("Chat Conversation", conversation, other_field)
    return {"other_read_at": str(other_read or "")}


@frappe.whitelist()
def messages(conversation, after=None, limit=100):
    """Full history, or only the messages newer than `after` (the browser keeps the last time it saw)."""
    me = _me()
    _member(conversation, me)
    filters = {"conversation": conversation}
    if after:
        filters["creation"] = [">", get_datetime(after)]
    rows = frappe.get_all(
        "Chat Message", filters=filters,
        fields=["name", "sender", "body", "book", "creation"],
        order_by="creation asc", limit_page_length=int(limit),
    )
    for r in rows:
        r["creation"] = str(r["creation"])
    return rows


@frappe.whitelist()
def send_message(conversation, body):
    me = _me()
    conv = _member(conversation, me)
    return _post(conversation, me, _body(body))


def _shop_users_for_book(book_id):
    """Shops with this book in their active catalog: [{user, business_name, city}]."""
    rows = frappe.db.sql("""
        select distinct bp.user as user, bp.name as business_profile, bp.business_name as business_name, bp.city as city
        from `tabBusinessCatalogItem` bci
        join `tabBusinessProfile` bp on bp.name = bci.business_profile
        where bci.book = %s and bci.is_active = 1 and ifnull(bp.user, '') != ''
    """, (book_id,), as_dict=True)
    return rows


@frappe.whitelist()
def book_shops(book_id):
    """The shops a buyer may ask about this book (only shops that hold it)."""
    _me()
    return _shop_users_for_book(book_id)


@frappe.whitelist()
def ask_about_book(book_id, shop_user=None, body="", shop_profile=None):
    """A question about a book goes only to a shop that holds it in its active catalog.
    The shop is given as its account (shop_user) or as its business profile (shop_profile)."""
    me = _me()
    if shop_profile and not shop_user:
        shop_user = frappe.db.get_value("BusinessProfile", shop_profile, "user")
    if not shop_user:
        frappe.throw(_("Choose a shop."))
    if shop_user == me:
        frappe.throw(_("Choose a shop other than your own."))
    holders = {r.user for r in _shop_users_for_book(book_id)}
    if shop_user not in holders:
        frappe.throw(_("This shop does not hold this book."), frappe.ValidationError)
    text = (body or "").strip() or _("Is this book available? / کیا یہ کتاب دستیاب ہے؟")
    conv = _conversation_for(me, shop_user)
    msg = _post(conv, me, _body(text), book=book_id)
    return {"conversation": conv, "message": msg}


@frappe.whitelist()
def add_contact(user, label=""):
    me = _me()
    if not user or user == me or not frappe.db.exists("User", user):
        frappe.throw(_("Choose another account."))
    name = frappe.db.get_value("Saved Contact", {"owner": me, "contact_user": user}, "name")
    if not name:
        name = frappe.get_doc({"doctype": "Saved Contact", "owner": me, "contact_user": user,
                               "label": (label or "").strip()[:140]}).insert(ignore_permissions=True).name
    return {"name": name, "contact_user": user, "contact_name": _display(user)}


@frappe.whitelist()
def remove_contact(user):
    me = _me()
    name = frappe.db.get_value("Saved Contact", {"owner": me, "contact_user": user}, "name")
    if name:
        frappe.delete_doc("Saved Contact", name, ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def my_contacts():
    me = _me()
    rows = frappe.get_all("Saved Contact", filters={"owner": me},
                          fields=["name", "contact_user", "label"], order_by="modified desc")
    return [{"name": r.name, "contact_user": r.contact_user, "label": r.label or "",
             "contact_name": _display(r.contact_user)} for r in rows]


def _card(user):
    """Name and phone of an account: the shop's details if it has a shop, else the buyer's."""
    bp = frappe.db.get_value("BusinessProfile", {"user": user}, ["business_name", "phone"], as_dict=True)
    bu = frappe.db.get_value("BuyerProfile", {"user": user}, ["full_name", "phone"], as_dict=True)
    name = (bp.business_name if bp else None) or (bu.full_name if bu else None) \
        or frappe.db.get_value("User", user, "full_name") or user
    phone = (bp.phone if bp else None) or (bu.phone if bu else None) or ""
    return {"name": name, "phone": phone}


@frappe.whitelist()
def contact_card(conversation):
    """The other side's name and WhatsApp number. Shown only after the other side has sent a message here."""
    me = _me()
    conv = _member(conversation, me)
    other = conv.user_b if conv.user_a == me else conv.user_a
    sent = frappe.db.exists("Chat Message", {"conversation": conversation, "sender": other})
    if not sent:
        return {"revealed": False}
    card = _card(other)
    return {"revealed": True, "name": card["name"], "phone": card["phone"]}
