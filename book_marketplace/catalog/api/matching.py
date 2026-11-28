"""Stage 1 matching API: publisher adds a book through the matching rule.

Dual write during stage 1: the existing Book record is still created (the live catalogue reads it),
and the new Work + Edition records are created beside it. See ref/STAGE1-MATCHING-DESIGN.md.
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from book_marketplace.catalog.matching import Candidate, Entry, compact, decide, normalize


def _candidates():
    rows = frappe.get_all(
        "Edition",
        fields=["name", "work", "publisher", "isbn", "volume_count", "visibility"],
        limit_page_length=0,
    )
    out = []
    for r in rows:
        w = frappe.db.get_value("Work", r.work, ["title", "author", "language"], as_dict=True)
        if not w:
            continue
        out.append(Candidate(
            work_id=r.work, title=w.title, author=w.author, edition_id=r.name,
            publisher=r.publisher, isbn=r.isbn, volumes=r.volume_count, language=w.language,
        ))
    return out


def _scored_json(scored):
    return json.dumps([
        {"edition": s.candidate.edition_id, "work": s.candidate.work_id, "score": s.score, "reasons": s.reasons}
        for s in scored
    ], ensure_ascii=False)


def _new_work(title, author, language):
    return frappe.get_doc({
        "doctype": "Work", "title": title, "author": author, "language": language,
        "title_compact": compact(title), "author_compact": compact(author),
    }).insert(ignore_permissions=True).name


def _edition_name(book_title, author, publisher):
    """Edition id = Book name, so existing links keep working. Mirrors the Book naming rule."""
    base = (book_title or "").strip()
    if not frappe.db.exists("Edition", base) and not frappe.db.exists("Book", base):
        return base
    candidate = f"{base} - {author}".strip()
    if not frappe.db.exists("Edition", candidate) and not frappe.db.exists("Book", candidate):
        return candidate
    candidate = f"{candidate} - {publisher}"
    n = 2
    name = candidate
    while frappe.db.exists("Edition", name) or frappe.db.exists("Book", name):
        name = f"{candidate} ({n})"
        n += 1
    return name


def _create_edition(work_id, entry, publisher, values, status_visibility="public"):
    """Create the Book (live catalogue) and the Edition (new level) with the same name."""
    name = _edition_name(entry.title, entry.author, publisher)
    book = frappe.get_doc({
        "doctype": "Book", "title": entry.title, "author": entry.author, "publisher": publisher,
        "book_type": values.get("book_type") or "Single Volume", "language": values.get("language") or "Urdu",
        "isbn": entry.isbn or None, "set_mrp": values.get("set_mrp"), "trade_price": values.get("trade_price"),
        "classifications": [{"category": c} for c in (values.get("categories") or [])],
        "languages": [{"language": l} for l in (values.get("languages") or [])],
        "is_bound": 1 if values.get("is_bound") else 0, "is_set": 1 if values.get("is_set") else 0,
        "volume_count": values.get("volume_count") or None, "part_count": values.get("part_count") or None,
        "binding": values.get("binding") or None, "page_count": values.get("page_count") or None,
        "weight": values.get("weight") or None, "colours": values.get("colours") or None,
        "global_visibility": 1,
    })
    book.flags.name_override = name
    book.name = name
    book.insert(ignore_permissions=True)
    edition = frappe.get_doc({
        "doctype": "Edition", "name": book.name, "work": work_id, "publisher": publisher,
        "isbn": entry.isbn or None, "volume_count": entry.volumes,
        "mrp": values.get("set_mrp"), "trade_price": values.get("trade_price"),
        "visibility": status_visibility, "created_by": publisher,
    })
    edition.insert(ignore_permissions=True)
    return book.name, edition.name


def _log(entry, decision, status, chosen_work=None, chosen_edition=None, decided_by="system", reason=""):
    return frappe.get_doc({
        "doctype": "MatchDecision", "status": status, "action": decision.action, "rule": decision.rule,
        "entry": json.dumps(entry.__dict__, ensure_ascii=False), "candidates": _scored_json(decision.candidates),
        "chosen_work": chosen_work, "chosen_edition": chosen_edition, "decided_by": decided_by,
        "decided_at": now_datetime(), "reason": reason,
    }).insert(ignore_permissions=True).name


@frappe.whitelist()
def _link_publisher_offer(profile_id, book_name, set_mrp=None, trade_price=None):
    """The publisher's offer on an existing original book (no new Book is created)."""
    if frappe.db.exists("Business Catalog Item", {"business_profile": profile_id, "book": book_name}):
        return {"status": "exists", "book": book_name,
                "message": _("This book is already in your list.")}
    item = frappe.get_doc({
        "doctype": "Business Catalog Item", "business_profile": profile_id, "book": book_name,
        "selling_price": set_mrp or 0, "trade_price": trade_price or 0, "is_active": 1,
        "added_on": now_datetime(),
    }).insert(ignore_permissions=True)
    return {"status": "linked", "book": book_name, "catalog_item": item.name,
            "message": _("Linked to the original book automatically.")}


def add_publisher_book(profile_id, title, author, book_type="Single Volume", language="Urdu", isbn=None,
                       set_mrp=None, trade_price=None, volumes=None, binding=None, page_count=None,
                       weight=None, colours=None, categories=None, is_bound=0, is_set=0,
                       volume_count=None, part_count=None, languages=None):
    """A publisher adds a book. The matching rule decides; nothing is merged silently."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    if frappe.db.get_value("BusinessProfile", profile_id, "profile_type") != "Publisher":
        frappe.throw(_("Only publishers can add books."), frappe.PermissionError)
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "catalog.edit")
    title = (title or "").strip()
    author = (author or "").strip()
    if not title or not author:
        frappe.throw(_("Title and author are required."))

    # A name that is an original's name or alias links the publisher to that original, automatically.
    from book_marketplace.catalog.doctype.book.book import find_book_by_alias
    original = find_book_by_alias(title)
    if original:
        return _link_publisher_offer(profile_id, original, set_mrp, trade_price)

    entry = Entry(title=title, author=author, publisher=profile_id, isbn=isbn,
                  volumes=int(volumes) if volumes else None, language=language)
    values = {"book_type": book_type, "language": language, "set_mrp": set_mrp, "trade_price": trade_price,
              "binding": binding, "page_count": page_count, "weight": weight, "colours": colours,
              "categories": categories or [], "is_bound": is_bound, "is_set": is_set,
              "volume_count": volume_count, "part_count": part_count, "languages": languages or []}
    decision = decide(entry, _candidates())

    if decision.action == "attach_edition":
        return {"status": "exists", "edition": decision.edition_id,
                "message": _("This book is already in the system. Add it to your list from search.")}

    if decision.action in ("new", "new_work"):
        work_id = _new_work(title, author, language)
        book_name, edition = _create_edition(work_id, entry, profile_id, values)
        _log(entry, decision, "auto", work_id, edition, decided_by=profile_id, reason=decision.rule)
        return {"status": "created", "edition": edition, "work": work_id, "rule": decision.rule}

    if decision.action == "new_edition_same_work":
        book_name, edition = _create_edition(decision.work_id, entry, profile_id, values)
        _log(entry, decision, "confirm", decision.work_id, edition, decided_by=profile_id,
             reason="same work, other publisher: confirm later")
        return {"status": "created_confirm", "edition": edition, "work": decision.work_id,
                "message": _("Added. The admin will confirm whether it is the same book as another publisher's.")}

    # queue
    name = _log(entry, decision, "pending", decided_by=profile_id, reason="medium match")
    return {"status": "queued", "decision": name,
            "message": _("This book looks similar to an existing one. The admin will confirm it.")}


@frappe.whitelist()
def list_pending_matches():
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only admin can view the review queue."), frappe.PermissionError)
    return frappe.get_all(
        "MatchDecision", filters={"status": "pending"},
        fields=["name", "entry", "candidates", "rule", "creation"], order_by="creation asc",
        limit_page_length=500,
    )


@frappe.whitelist()
def resolve_match(decision_id, action, edition_id=None):
    """Admin decision on a queued match. action: 'same' (attach to edition_id), 'new' (create), 'reject'."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only admin can resolve matches."), frappe.PermissionError)
    md = frappe.get_doc("MatchDecision", decision_id)
    if md.status != "pending":
        frappe.throw(_("This item is already resolved."))
    entry = Entry(**json.loads(md.entry))
    user = frappe.session.user
    if action == "same":
        if not edition_id or not frappe.db.exists("Edition", edition_id):
            frappe.throw(_("Choose an existing edition."))
        work = frappe.db.get_value("Edition", edition_id, "work")
        md.db_set({"status": "resolved", "chosen_work": work, "chosen_edition": edition_id,
                   "decided_by": user, "decided_at": now_datetime(), "reason": "admin: same book"})
        return {"status": "resolved", "edition": edition_id}
    if action == "new":
        work_id = _new_work(entry.title, entry.author, entry.language)
        values = {"book_type": "Single Volume", "language": entry.language or "Urdu"}
        book_name, edition = _create_edition(work_id, entry, entry.publisher, values)
        md.db_set({"status": "resolved", "chosen_work": work_id, "chosen_edition": edition,
                   "decided_by": user, "decided_at": now_datetime(), "reason": "admin: different book"})
        return {"status": "resolved", "edition": edition}
    if action == "reject":
        md.db_set({"status": "resolved", "decided_by": user, "decided_at": now_datetime(),
                   "reason": "admin: rejected"})
        return {"status": "rejected"}
    frappe.throw(_("Unknown action."))
