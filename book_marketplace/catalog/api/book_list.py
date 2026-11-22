"""The business's book list: every book it holds (published by it, added by it, or in its own store catalog),
with the stock it has, and whether it is published on its own store.
Filters: availability (all, available, unavailable), publish state (all, published, unpublished), item type and text.
The text also matches ISBN, barcode and article number exactly, so a scanned code finds its book.
Sort: "alpha" (by title; for the same title the cheapest first) or "price" (cheapest first).
Each row carries publisher_count: how many publishers hold this same book (same ISBN, or same title and author)."""
from collections import defaultdict

import frappe

from book_marketplace.auth.api.auth import _assert_profile_owner


def _book_key(b):
    isbn = (b.isbn or "").strip()
    if isbn:
        return "isbn:" + isbn.lower()
    return "t:" + (b.title or "").strip().lower() + "|" + (b.author or "").strip().lower()


@frappe.whitelist()
def book_list(profile_id, text="", availability="all", published="all", limit=50, offset=0, item_type="", sort="alpha"):
    _assert_profile_owner(profile_id)
    limit = min(frappe.utils.cint(limit) or 50, 200)
    offset = frappe.utils.cint(offset)

    catalog = {r.book: r for r in frappe.get_all(
        "BusinessCatalogItem", filters={"business_profile": profile_id},
        fields=["name", "book", "is_active"], limit_page_length=0)}
    names = set(catalog)
    names |= set(frappe.get_all("Book", filters={"publisher": profile_id}, pluck="name", limit_page_length=0))
    names |= set(frappe.get_all("Book", filters={"owner_profile": profile_id}, pluck="name", limit_page_length=0))
    if not names:
        return {"books": [], "total": 0}

    held = {}
    for row in frappe.get_all("StockLedger", filters={"business_profile": profile_id, "state": "Available",
                                                      "book": ["in", list(names)]},
                              fields=["book", "quantity"], limit_page_length=0):
        held[row.book] = held.get(row.book, 0) + float(row.quantity or 0)

    books = frappe.get_all("Book", filters={"name": ["in", list(names)]},
                           fields=["name", "title", "title_en", "author", "publisher", "item_type", "isbn", "barcode",
                                   "article_number", "language", "binding", "edition", "set_mrp"],
                           order_by="title asc", limit_page_length=0)

    # publishers per book: counted over every book this business holds, before any filter
    publishers_of = defaultdict(set)
    for b in books:
        publishers_of[_book_key(b)].add(b.publisher or b.name)

    pub_ids = list({b.publisher for b in books if b.publisher})
    pub_names = {p.name: p.business_name for p in frappe.get_all(
        "BusinessProfile", filters={"name": ["in", pub_ids]}, fields=["name", "business_name"], limit_page_length=0)} if pub_ids else {}

    t = (text or "").strip().lower()
    out = []
    for b in books:
        if t:
            codes = {(b.isbn or "").strip().lower(), (b.barcode or "").strip().lower(), (b.article_number or "").strip().lower()}
            if t not in (b.title or "").lower() and t not in (b.author or "").lower() and t not in codes:
                continue
        if item_type and (b.item_type or "Book") != item_type:
            continue
        qty = held.get(b.name, 0)
        cat = catalog.get(b.name)
        is_published = bool(cat and cat.is_active)
        if availability == "available" and qty <= 0:
            continue
        if availability == "unavailable" and qty > 0:
            continue
        if published == "published" and not is_published:
            continue
        if published == "unpublished" and is_published:
            continue
        out.append({"name": b.name, "title": b.title, "title_en": b.title_en or "", "author": b.author or "",
                    "publisher": b.publisher or "", "publisher_name": pub_names.get(b.publisher, "") or b.publisher or "",
                    "publisher_count": len(publishers_of[_book_key(b)]),
                    "isbn": b.isbn or "", "barcode": b.barcode or "", "language": b.language or "", "binding": b.binding or "",
                    "edition": b.edition or "", "mrp": float(b.set_mrp or 0),
                    "quantity": qty, "available": qty > 0, "published": is_published, "item_type": b.item_type or "Book",
                    "catalog_item": cat.name if cat else None})

    # no price (0) always goes last
    def price_key(r):
        return (r["mrp"] <= 0, r["mrp"])

    if sort == "price":
        out.sort(key=lambda r: price_key(r) + ((r["title"] or "").lower(),))
    else:
        out.sort(key=lambda r: ((r["title"] or "").lower(),) + price_key(r))
    return {"books": out[offset:offset + limit], "total": len(out)}
