"""Books offered by readers and by businesses that are old, rare, manuscript or damaged.

- Public list: only Available listings, and never the phone, payment details or the owner's account.
- Phone and payment details are kept on the listing; they are shown only to the accepted buyer
  (the buyer request step comes with the exchange feature, later).
- Owner: the signed-in user who made the listing. Only the owner changes or marks it given.
- Photos: up to six (photo_1 to photo_6), each optional.
- Condition details: pages, edition, colour, a defect from a list, and a free note for the defect.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

CONDITIONS = ("Used", "Rare", "Manuscript")
KINDS = ("For sale", "Gift", "Available (price later)")
GRADES = ("Excellent", "Good", "Usable", "Damaged / worn")
DELIVERY = ("Pickup", "Courier", "Both")
DEFECTS = ("Water damaged", "Aged", "Torn", "Stained", "Binding broken", "Pages missing", "Worm-eaten", "Marginalia", "Used", "Other")
PUBLIC_FIELDS = ["name", "title", "author", "book", "condition", "condition_grade", "kind", "price",
                 "description", "city", "area", "delivery", "pages", "edition", "colour", "defect", "defect_note",
                 "photo_1", "photo_2", "photo_3", "photo_4", "photo_5", "photo_6",
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
                   owner_profile=None, pages=None, edition=None, colour=None, defect=None, defect_note=None):
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
    if defect and defect not in DEFECTS:
        frappe.throw(_("Defect must be one of: {0}").format(", ".join(DEFECTS)))
    if pages not in (None, "") and int(pages) < 0:
        frappe.throw(_("Pages cannot be negative."))
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
        "pages": int(pages) if pages not in (None, "") else None, "edition": (edition or "").strip()[:140] or None,
        "colour": (colour or "").strip()[:80] or None, "defect": defect or None,
        "defect_note": (defect_note or "").strip()[:500] or None,
        "created_on": now_datetime(),
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


def _buyer_or_none():
    return frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name") if frappe.session.user != "Guest" else None


def _add_likes(rows):
    """Adds `likes` (count) and `liked` (by this buyer) to each listing row."""
    names = [r["name"] for r in rows]
    counts = {}
    if names:
        for listing, n in frappe.db.sql("select listing, count(*) from `tabReader Listing Like` where listing in %(n)s group by listing",
                                        {"n": names}):
            counts[listing] = n
    buyer = _buyer_or_none()
    mine = set(frappe.get_all("Reader Listing Like", filters={"buyer_profile": buyer, "listing": ["in", names]},
                              pluck="listing")) if (buyer and names) else set()
    for r in rows:
        r["likes"] = counts.get(r["name"], 0)
        r["liked"] = r["name"] in mine
    return rows


@frappe.whitelist()
def like_listing(name, unlike=0):
    """A buyer likes (or removes the like on) an available used or rare listing."""
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if not buyer:
        frappe.throw(_("Log in as a buyer to like a book."), frappe.PermissionError)
    if not frappe.db.exists("Reader Listing", {"name": name, "status": "Available"}):
        frappe.throw(_("This listing is not available."))
    existing = frappe.db.get_value("Reader Listing Like", {"listing": name, "buyer_profile": buyer}, "name")
    if int(unlike or 0):
        if existing:
            frappe.delete_doc("Reader Listing Like", existing, ignore_permissions=True, force=True)
    elif not existing:
        frappe.get_doc({"doctype": "Reader Listing Like", "listing": name, "buyer_profile": buyer,
                        "liked_on": now_datetime()}).insert(ignore_permissions=True)
    return {"name": name, "liked": not int(unlike or 0),
            "likes": frappe.db.count("Reader Listing Like", {"listing": name})}


@frappe.whitelist()
def my_liked_listings():
    """The available listings this buyer has liked, newest like first."""
    buyer = _buyer_or_none()
    if not buyer:
        return []
    names = frappe.get_all("Reader Listing Like", filters={"buyer_profile": buyer}, pluck="listing",
                           order_by="liked_on desc", limit_page_length=200)
    if not names:
        return []
    rows = frappe.get_all("Reader Listing", filters={"name": ["in", names], "status": "Available"},
                          fields=PUBLIC_FIELDS)
    order = {n: i for i, n in enumerate(names)}
    rows.sort(key=lambda r: order.get(r["name"], 0))
    return _add_likes(rows)


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
    return _add_likes(rows)


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
