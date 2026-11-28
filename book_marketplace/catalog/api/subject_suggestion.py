import frappe
from frappe import _
from frappe.utils import now

MISC = "متفرق"
AGREE_TO_APPLY = 2  # distinct people suggesting the same subject


def _profiles_of(user):
    profiles = set(frappe.get_all("BusinessProfile", filters={"user": user}, pluck="name"))
    profiles |= set(frappe.get_all("BusinessMember", filters={"user": user, "is_active": 1}, pluck="business_profile"))
    return profiles


def _suggester(user, book):
    """(kind, profile): Publisher when the user's business publishes this book (or is a publisher that lists it),
    Shop when the user has a business, else Reader."""
    profiles = _profiles_of(user)
    if not profiles:
        return "Reader", None
    pub = frappe.db.get_value("Book", book, "publisher")
    if pub and pub in profiles:
        return "Publisher", pub
    listing = [p for p in profiles if frappe.db.exists("Business Catalog Item", {"business_profile": p, "book": book})]
    for p in listing:
        if frappe.db.get_value("BusinessProfile", p, "profile_type") == "Publisher":
            return "Publisher", p
    return "Shop", (listing[0] if listing else sorted(profiles)[0])


def _apply(book, subject, reviewer):
    """Give the book this subject, take it out of متفرق, and accept every pending suggestion that agrees."""
    if not frappe.db.exists("BookClassification", {"parent": book, "parenttype": "Book", "category": subject}):
        idx = (frappe.db.sql("select ifnull(max(idx),0) from tabBookClassification where parent=%s and parenttype='Book'",
                             (book,))[0][0] or 0) + 1
        ts = now()
        frappe.db.sql("""insert into tabBookClassification (name, creation, modified, modified_by, owner, docstatus, idx,
                category, parent, parentfield, parenttype) values (%s, %s, %s, %s, %s, 0, %s, %s, %s, 'classifications', 'Book')""",
                      (frappe.generate_hash(length=12), ts, ts, reviewer, reviewer, idx, subject, book))
    frappe.db.sql("delete from tabBookClassification where parent=%s and parenttype='Book' and category=%s", (book, MISC))
    frappe.db.sql("update tabBook set modified=%s where name=%s", (now(), book))
    for name in frappe.get_all("Book Subject Suggestion", filters={"book": book, "subject": subject, "status": "Pending"}, pluck="name"):
        frappe.db.set_value("Book Subject Suggestion", name, {"status": "Accepted", "reviewed_by": reviewer})


@frappe.whitelist()
def suggest_subject(book, subject, note=None):
    """Any logged-in reader, shop or publisher suggests a subject for a book.
    One open suggestion per person per book (a new one replaces it)."""
    user = frappe.session.user
    if user == "Guest":
        frappe.throw(_("Please log in to suggest a subject."), frappe.PermissionError)
    if not frappe.db.exists("Book", book):
        frappe.throw(_("Book not found."))
    cat = frappe.db.get_value("Book Category", subject, ["category_type"], as_dict=True)
    if not cat or cat.category_type != "Subject" or subject == MISC:
        frappe.throw(_("Pick a subject from the list."))
    kind, profile = _suggester(user, book)
    existing = frappe.db.get_value("Book Subject Suggestion", {"book": book, "suggested_by": user, "status": "Pending"}, "name")
    doc = frappe.get_doc("Book Subject Suggestion", existing) if existing else frappe.new_doc("Book Subject Suggestion")
    doc.update({"book": book, "subject": subject, "note": (note or "").strip()[:500], "suggested_by": user,
                "business_profile": profile, "suggester_type": kind, "status": "Pending"})
    doc.save(ignore_permissions=True)
    agree = len(set(frappe.get_all("Book Subject Suggestion",
                                   filters={"book": book, "subject": subject, "status": ["in", ["Pending", "Accepted"]]},
                                   pluck="suggested_by")))
    applied = kind == "Publisher" or agree >= AGREE_TO_APPLY
    if applied:
        _apply(book, subject, user)
    return {"name": doc.name, "status": "Accepted" if applied else "Pending", "suggested_as": kind, "agree": agree}


@frappe.whitelist()
def my_suggestion(book):
    """The current user's latest suggestion for this book, for the book page."""
    if frappe.session.user == "Guest":
        return None
    rows = frappe.get_all("Book Subject Suggestion", filters={"book": book, "suggested_by": frappe.session.user},
                          fields=["name", "subject", "status", "suggester_type"], order_by="modified desc", limit=1)
    return rows[0] if rows else None


@frappe.whitelist(allow_guest=True)
def subject_options():
    """Subjects for the picker, in tree order, with their parents (متفرق left out)."""
    rows = frappe.get_all("Book Category", filters={"category_type": "Subject"},
                          fields=["name", "category_name_ur", "parent_book_category", "is_group"],
                          order_by="lft asc", limit_page_length=0)
    return [{"name": r.name, "label": r.category_name_ur or r.name, "parent": r.parent_book_category or "",
             "is_group": int(r.is_group or 0)} for r in rows if r.name != MISC]


@frappe.whitelist()
def review_suggestion(name, action):
    """Admin accepts or rejects a pending suggestion."""
    frappe.only_for("System Manager")
    doc = frappe.get_doc("Book Subject Suggestion", name)
    if action == "accept":
        _apply(doc.book, doc.subject, frappe.session.user)
    elif action == "reject":
        doc.db_set({"status": "Rejected", "reviewed_by": frappe.session.user})
    else:
        frappe.throw(_("Unknown action."))
    return frappe.db.get_value("Book Subject Suggestion", name, "status")


@frappe.whitelist()
def uncategorized_books(profile_id, text=None, limit=50, offset=0):
    """This business's books (its own and those in its list) that still sit in متفرق, for the panel's subject page.
    Each row carries the user's own open suggestion, if any."""
    from book_marketplace.auth.permissions import assert_access
    assert_access(profile_id, "catalog.view")
    limit = min(int(limit or 50), 200)
    offset = int(offset or 0)
    cond, args = "", {"p": profile_id, "m": MISC}
    if text and text.strip():
        cond = " and (b.title like %(t)s or b.name like %(t)s or b.author like %(t)s)"
        args["t"] = f"%{text.strip()}%"
    base = f"""from tabBook b join tabBookClassification c on c.parent=b.name and c.parenttype='Book' and c.category=%(m)s
        where (b.publisher=%(p)s or exists (select 1 from `tabBusiness Catalog Item` i where i.book=b.name and i.business_profile=%(p)s)){cond}"""
    total = frappe.db.sql(f"select count(distinct b.name) {base}", args)[0][0]
    rows = frappe.db.sql(f"select distinct b.name, b.title, b.author {base} order by b.title limit {limit} offset {offset}", args, as_dict=True)
    mine = {}
    if rows and frappe.session.user != "Guest":
        for s in frappe.get_all("Book Subject Suggestion", filters={"book": ["in", [r.name for r in rows]],
                                "suggested_by": frappe.session.user, "status": "Pending"}, fields=["book", "subject"]):
            mine[s.book] = s.subject
    return {"total": total, "books": [{**r, "my_subject": mine.get(r.name, "")} for r in rows]}
