"""Books offered by readers and by businesses that are old, rare, manuscript or damaged.

- Public list: only Available listings, and never the phone, payment details or the owner's account.
- Phone and payment details are kept on the listing; they are shown only to the accepted buyer
  (the buyer request step comes with the exchange feature, later).
- Owner: the signed-in user who made the listing. Only the owner changes or marks it given.
- Photos: up to six (photo_1 to photo_6), each optional.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

CONDITIONS = ("Used", "Rare", "Manuscript")
KINDS = ("For sale", "Gift", "Available (price later)")
GRADES = ("Excellent", "Good", "Usable", "Damaged / worn")
DELIVERY = ("Pickup", "Courier", "Both")
PUBLIC_FIELDS = ["name", "title", "author", "book", "condition", "condition_grade", "kind", "price",
                 "description", "city", "area", "delivery", "photo_1", "photo_2", "photo_3", "photo_4", "photo_5", "photo_6",
                 "status", "created_on", "owner_profile"]


def _signed_in():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Sign in to list a book."), frappe.AuthenticationError)
    return user


def _choice(value, allowed, field):
    if value not in allowed:
        frappe.throw(_("{0} must be one of: {1}").format(field, ", ".join(allowed)))
    return value


@frappe.whitelist()
def create_listing(title, condition, kind, city, condition_grade=None, author=None, book=None, price=0,
                   description=None, area=None, delivery="Pickup", phone=None, bank_info=None,
                   photo_1=None, photo_2=None, photo_3=None, photo_4=None, photo_5=None, photo_6=None,
                   owner_profile=None):
    user = _signed_in()
    title = (title or "").strip()
    if not title:
        frappe.throw(_("Write the title."))
    if not (city or "").strip():
        frappe.throw(_("Write the city."))
    _choice(condition, CONDITIONS, "Condition")
    _choice(kind, KINDS, "Kind")
    if condition_grade:
        _choice(condition_grade, GRADES, "Grade")
    _choice(delivery or "Pickup", DELIVERY, "Delivery")
    if owner_profile and frappe.db.get_value("BusinessProfile", owner_profile, "user") != user:
        frappe.throw(_("This business is not yours."), frappe.PermissionError)
    doc = frappe.get_doc({
        "doctype": "Reader Listing", "owner_user": user, "owner_profile": owner_profile or None,
        "status": "Available", "kind": kind, "condition": condition, "condition_grade": condition_grade or None,
        "title": title[:140], "author": (author or "").strip()[:140] or None, "book": book or None,
        "price": 0 if kind == "Gift" else (frappe.utils.flt(price) or 0),
        "description": (description or "")[:1000] or None, "city": city.strip()[:80], "area": (area or "").strip()[:80] or None,
        "delivery": delivery or "Pickup", "phone": (phone or "").strip()[:30] or None,
        "bank_info": (bank_info or "")[:500] or None,
        "photo_1": photo_1 or None, "photo_2": photo_2 or None, "photo_3": photo_3 or None,
        "photo_4": photo_4 or None, "photo_5": photo_5 or None, "photo_6": photo_6 or None,
        "created_on": now_datetime(),
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def public_listings(city=None, condition=None, kind=None, limit=30, offset=0):
    filters = {"status": "Available"}
    if city:
        filters["city"] = city
    if condition in CONDITIONS:
        filters["condition"] = condition
    if kind in KINDS:
        filters["kind"] = kind
    limit = min(frappe.utils.cint(limit) or 30, 100)
    rows = frappe.get_all("Reader Listing", filters=filters, fields=PUBLIC_FIELDS,
                          order_by="created_on desc", limit_start=frappe.utils.cint(offset), limit_page_length=limit)
    return rows


@frappe.whitelist()
def my_listings():
    user = _signed_in()
    return frappe.get_all("Reader Listing", filters={"owner_user": user},
                          fields=PUBLIC_FIELDS + ["phone", "bank_info"], order_by="created_on desc", limit_page_length=200)


@frappe.whitelist()
def set_listing_status(name, status):
    user = _signed_in()
    if status not in ("Available", "Given", "Withdrawn"):
        frappe.throw(_("Status must be Available, Given or Withdrawn."))
    doc = frappe.get_doc("Reader Listing", name)
    if doc.owner_user != user:
        frappe.throw(_("Only the owner can change this listing."), frappe.PermissionError)
    doc.status = status
    if status == "Given":
        doc.given_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}
