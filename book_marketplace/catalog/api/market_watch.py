"""Market watch (a paid tool): for a business, which books the other businesses hold few copies of. For each book,
the copies held by every other business (in total, and how many businesses hold any). Only totals are shown.
Opens only for a business whose paid badge (verified_badge) an admin has turned on."""
import frappe

from book_marketplace.auth.api.auth import _assert_profile_owner


@frappe.whitelist()
def market_watch(profile_id, text="", max_quantity=None, publishers=None, limit=50, offset=0):
    """Books sorted by copies in the market, fewest first (a book nobody holds comes first).
    text: title or author. max_quantity: only books with at most this many copies in the market.
    publishers: the publisher(s) whose books to check (a list, or one id); empty means all."""
    _assert_profile_owner(profile_id)
    if not frappe.db.get_value("BusinessProfile", profile_id, "verified_badge"):
        frappe.throw(frappe._("Market Watch is a paid tool. It opens after payment for your business."))
    limit = min(frappe.utils.cint(limit) or 50, 200)
    offset = frappe.utils.cint(offset)
    cap = None if max_quantity in (None, "") else float(max_quantity)

    if isinstance(publishers, str):
        publishers = frappe.parse_json(publishers) if publishers.strip().startswith("[") else [publishers]
    publishers = [p for p in (publishers or []) if p]

    filters = {"global_visibility": 1}
    if publishers:
        filters["publisher"] = ["in", publishers]
    books = frappe.get_all("Book", filters=filters, fields=["name", "title", "author"], limit_page_length=0)
    t = (text or "").strip().lower()
    if t:
        books = [b for b in books if t in (b.title or "").lower() or t in (b.author or "").lower()]
    if not books:
        return {"books": [], "total": 0}

    held = {}
    for row in frappe.get_all("StockLedger", filters={"book": ["in", [b.name for b in books]], "state": "Available"},
                              fields=["book", "business_profile", "quantity"], limit_page_length=0):
        held.setdefault(row.book, {})
        held[row.book][row.business_profile] = held[row.book].get(row.business_profile, 0) + float(row.quantity or 0)

    rows = []
    for b in books:
        # every business except this one: what the market holds for a shop to buy from
        others = {p: q for p, q in held.get(b.name, {}).items() if p != profile_id and q > 0}
        market = sum(others.values())
        if cap is not None and market > cap:
            continue
        rows.append({"book": b.name, "title": b.title, "author": b.author,
                     "market_quantity": market, "holders": len(others)})
    rows.sort(key=lambda r: (r["market_quantity"], (r["title"] or "").lower()))
    return {"books": rows[offset:offset + limit], "total": len(rows)}
