"""What a publisher's books are worth in the market: for each book the publisher prints, how many copies the
publisher holds itself and how many copies the other businesses hold (in total and how many businesses hold any).
A shop's own names are not shown: only the totals, so the publisher sees the market without the shop list."""
import frappe

from book_marketplace.auth.api.auth import _assert_profile_owner


@frappe.whitelist()
def market_stock(profile_id, text="", limit=50, offset=0):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "reports.view")
    if frappe.db.get_value("BusinessProfile", profile_id, "profile_type") != "Publisher":
        frappe.throw(frappe._("Only a publisher has market stock."))
    limit = min(frappe.utils.cint(limit) or 50, 200)
    offset = frappe.utils.cint(offset)

    books = frappe.get_all("Book", filters={"publisher": profile_id},
                           fields=["name", "title", "author"], order_by="title asc", limit_page_length=0)
    t = (text or "").strip().lower()
    if t:
        books = [b for b in books if t in (b.title or "").lower() or t in (b.author or "").lower()]
    total = len(books)
    page = books[offset:offset + limit]
    if not page:
        return {"books": [], "total": total}

    # copies held (state Available) for the page's books, by business
    held = {}
    for row in frappe.get_all("StockLedger", filters={"book": ["in", [b.name for b in page]], "state": "Available"},
                              fields=["book", "business_profile", "quantity"], limit_page_length=0):
        held.setdefault(row.book, {})
        held[row.book][row.business_profile] = held[row.book].get(row.business_profile, 0) + float(row.quantity or 0)

    rows = []
    for b in page:
        by_business = held.get(b.name, {})
        own = by_business.get(profile_id, 0)
        others = {p: q for p, q in by_business.items() if p != profile_id and q > 0}
        rows.append({
            "book": b.name, "title": b.title, "author": b.author,
            "own_quantity": own,
            "market_quantity": sum(others.values()),
            "holders": len(others),
        })
    return {"books": rows, "total": total}
