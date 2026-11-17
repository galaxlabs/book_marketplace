import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def search(text=None, subject=None, publisher=None, author=None, language=None, board=None, book_type=None, limit=20, offset=0):
    filters = {"global_visibility": 1}
    if text:
        filters["title"] = ["like", f"%{text}%"]
    if publisher:
        filters["publisher"] = publisher
    if author:
        filters["author"] = ["like", f"%{author}%"]
    if language:
        filters["language"] = language
    if board:
        filters["board"] = ["like", f"%{board}%"]
    if book_type:
        filters["book_type"] = book_type
    books = frappe.get_all(
        "Book",
        filters=filters,
        fields=[
            "name", "title", "subtitle", "author", "author_profile", "publisher",
            "book_type", "isbn", "language", "set_mrp", "primary_category",
            "genre", "religious_segment", "introduction", "creation"
        ],
        limit=limit,
        start=offset,
        order_by="modified desc",
    )
    for book in books:
        publisher_name = frappe.db.get_value("BusinessProfile", book.publisher, "business_name")
        book.publisher_name = publisher_name
    return {"books": books, "total": len(books)}


@frappe.whitelist(allow_guest=True)
def get_book(book_id):
    book = frappe.get_doc("Book", book_id)
    subjects = book.get("subjects", [])
    volumes = book.get("volumes", [])
    variants = book.get("variants", [])
    is_multi = book.book_type == "Multi Volume"
    loose_volumes = [v for v in volumes if v.loose_sale] if is_multi else []
    stock_summary = get_stock_summary(book_id, volumes)
    return {
        "book": book.as_dict(),
        "subjects": [s.as_dict() for s in subjects],
        "volumes": [v.as_dict() for v in volumes] if is_multi else [],
        "variants": [v.as_dict() for v in variants],
        "loose_volumes": [v.as_dict() for v in loose_volumes],
        "is_multi_volume": is_multi,
        "volume_count": len(volumes) if is_multi else 0,
        "stock_summary": stock_summary,
    }


@frappe.whitelist(allow_guest=True)
def lookup_by_barcode(barcode):
    for field in ("barcode", "isbn"):
        book_name = frappe.db.get_value("Book", {field: barcode}, "name")
        if book_name:
            return get_book(book_name)
    frappe.throw(_("No book found with this barcode/ISBN"))


def get_stock_summary(book_id, volumes=None):
    available = frappe.get_all(
        "StockLedger",
        filters={"book": book_id, "state": "Available", "quantity": [">", 0]},
        fields=["business_profile", "branch_location", "quantity", "volume"],
    )
    total_available = sum(s.quantity for s in available)
    return {"total_available": total_available, "seller_count": len(available)}


# ─── Business Catalog (own store) ─────────────────────────

@frappe.whitelist()
def add_to_catalog(profile_id, book_id, selling_price=None, trade_price=None):
    if frappe.db.exists("BusinessCatalogItem", {"business_profile": profile_id, "book": book_id}):
        return {"error": "Book already in catalog"}
    doc = frappe.get_doc({
        "doctype": "BusinessCatalogItem",
        "business_profile": profile_id,
        "book": book_id,
        "selling_price": selling_price,
        "trade_price": trade_price,
        "is_active": 1,
    })
    doc.insert()
    return {"name": doc.name, "book": book_id, "status": "added"}


@frappe.whitelist()
def remove_from_catalog(item_id):
    frappe.delete_doc("BusinessCatalogItem", item_id)
    return {"status": "removed"}


def _stock_for(book_id, profile_id):
    return frappe.db.get_value(
        "StockLedger",
        {"book": book_id, "business_profile": profile_id, "state": "Available"},
        "sum(quantity)",
    ) or 0


@frappe.whitelist()
def list_catalog(profile_id, active_only=True):
    """Catalog rows with general (selling) and trade prices.
    A publisher also sees its own visible books as OWN-<book> rows."""
    filters = {"business_profile": profile_id}
    if frappe.utils.cint(active_only):
        filters["is_active"] = 1
    items = frappe.get_all(
        "BusinessCatalogItem",
        filters=filters,
        fields=["name", "book", "selling_price", "trade_price", "is_active", "added_on"],
        order_by="added_on desc",
    )
    rows, listed = [], set()
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            ["title", "author", "book_type", "set_mrp", "trade_price", "language", "genre", "religious_segment"],
            as_dict=True,
        ) or frappe._dict()
        listed.add(item.book)
        rows.append({
            "name": item.name,
            "book": item.book,
            "book_title": book.title or "",
            "book_author": book.author or "",
            "book_type": book.book_type or "",
            "book_mrp": book.set_mrp,
            "book_trade_price": book.trade_price,
            "language": book.language or "",
            "genre": book.genre or "",
            "religious_segment": book.religious_segment or "",
            "selling_price": item.selling_price,
            "trade_price": item.trade_price,
            "is_active": item.is_active,
            "added_on": item.added_on,
            "owned": 0,
            "stock_available": _stock_for(item.book, profile_id),
        })
    if frappe.db.get_value("BusinessProfile", profile_id, "profile_type") == "Publisher":
        own = frappe.get_all(
            "Book",
            filters={"publisher": profile_id, "global_visibility": 1},
            fields=["name", "title", "author", "book_type", "set_mrp", "trade_price", "language", "genre", "religious_segment"],
            order_by="title asc",
            limit_page_length=5000,
        )
        for b in own:
            if b.name in listed:
                continue
            rows.append({
                "name": "OWN-" + b.name,
                "book": b.name,
                "book_title": b.title or "",
                "book_author": b.author or "",
                "book_type": b.book_type or "",
                "book_mrp": b.set_mrp,
                "book_trade_price": b.trade_price,
                "language": b.language or "",
                "genre": b.genre or "",
                "religious_segment": b.religious_segment or "",
                "selling_price": b.set_mrp,
                "trade_price": b.trade_price,
                "is_active": 1,
                "added_on": None,
                "owned": 1,
                "stock_available": _stock_for(b.name, profile_id),
            })
    return rows


@frappe.whitelist()
def update_catalog_price(item_id, selling_price=None, trade_price=None, profile_id=None):
    from book_marketplace.auth.api.auth import _assert_profile_owner

    if str(item_id).startswith("OWN-"):
        book_id = str(item_id)[4:]
        book = frappe.get_doc("Book", book_id)
        if profile_id and profile_id != book.publisher:
            frappe.throw(_("Not permitted for this book."), frappe.PermissionError)
        _assert_profile_owner(book.publisher)
        if selling_price is not None:
            book.set_mrp = selling_price
        if trade_price is not None:
            book.trade_price = trade_price
        book.save()
        return {"name": item_id, "selling_price": book.set_mrp, "trade_price": book.trade_price}

    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    _assert_profile_owner(doc.business_profile)
    if selling_price is not None:
        doc.selling_price = selling_price
    if trade_price is not None:
        doc.trade_price = trade_price
    doc.save()
    return {"name": doc.name, "selling_price": doc.selling_price, "trade_price": doc.trade_price}


@frappe.whitelist()
def toggle_catalog_item(item_id, is_active):
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    doc.is_active = is_active
    doc.save()
    return {"name": doc.name, "is_active": is_active}


@frappe.whitelist()
def create_own_book(profile_id, title, author, book_type="Single Volume", language="Urdu", isbn=None,
                    set_mrp=None, trade_price=None, binding=None, page_count=None, weight=None, colours=None):
    """Panel entry point for a publisher's new book. Goes through the matching rule (stage 1).
    Response shape kept for the panel: {name, title, status} on success, {error, existing} on refusal."""
    from book_marketplace.catalog.api.matching import add_publisher_book

    res = add_publisher_book(
        profile_id=profile_id, title=title, author=author, book_type=book_type or "Single Volume",
        language=language or "Urdu", isbn=isbn, set_mrp=set_mrp, trade_price=trade_price,
        binding=binding, page_count=page_count, weight=weight, colours=colours,
    )
    if res.get("status") == "exists":
        return {"error": res.get("message"), "existing": res.get("edition")}
    if res.get("status") == "queued":
        # Shown as a message in the panel; the admin confirms it before it is listed.
        return {"error": res.get("message"), "status": "queued", "decision": res.get("decision")}
    return {"name": res.get("edition"), "title": title.strip(), "status": "created",
            "message": res.get("message")}


# ─── Business Store (public storefront) ───────────────────

@frappe.whitelist(allow_guest=True)
def get_store_by_slug(slug):
    store = frappe.db.get_value(
        "BusinessStore",
        {"slug": slug, "is_published": 1},
        ["name", "business_profile", "store_name", "description", "history", "key_info", "banner_image",
         "contact_phone", "contact_email", "address", "city"],
        as_dict=True,
    )
    if not store:
        return None
    profile = frappe.db.get_value(
        "BusinessProfile", store.business_profile,
        ["business_name", "profile_type", "city"],
        as_dict=True,
    )
    store.business_name = profile.business_name if profile else ""
    store.profile_type = profile.profile_type if profile else ""
    items = _store_items(store.business_profile, store.profile_type)
    catalog = []
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            [
                "title", "subtitle", "author", "book_type", "set_mrp", "language",
                "binding", "genre", "religious_segment", "introduction"
            ],
            as_dict=True,
        )
        stock = frappe.db.get_value(
            "StockLedger",
            {"book": item.book, "business_profile": store.business_profile, "state": "Available"},
            "sum(quantity)",
        )
        # A publisher's own book with no stock record has no quantity to show (stock is None),
        # and the store treats it as available. Once any record exists, its quantity decides, 0 included.
        tracked = True
        if str(item.name).startswith("OWN-"):
            tracked = bool(frappe.db.exists(
                "StockLedger", {"book": item.book, "business_profile": store.business_profile}
            ))
        stock = None if not tracked else (stock or 0)
        catalog.append({
            "item_name": item.name,
            "book_id": item.book,
            "title": book.title if book else "",
            "subtitle": book.subtitle if book else "",
            "author": book.author if book else "",
            "book_type": book.book_type if book else "",
            "mrp": book.set_mrp if book else None,
            "selling_price": item.selling_price,
            "language": book.language if book else "",
            "genre": book.genre if book else "",
            "religious_segment": book.religious_segment if book else "",
            "introduction": book.introduction if book else "",
            "stock": stock,
        })
    store.catalog = catalog
    store.catalog_count = len(catalog)
    return store


def _store_items(profile_name, profile_type):
    """Active catalog rows for a store. A publisher's own visible books are added too,
    with no selling price, so the store shows MRP for them."""
    items = list(frappe.get_all(
        "BusinessCatalogItem",
        filters={"business_profile": profile_name, "is_active": 1},
        fields=["name", "book", "selling_price"],
        order_by="modified desc",
    ))
    if profile_type != "Publisher":
        return items
    listed = {i.book for i in items}
    own = frappe.get_all(
        "Book",
        filters={"publisher": profile_name, "global_visibility": 1},
        fields=["name"],
        order_by="modified desc",
    )
    for book in own:
        if book.name not in listed:
            items.append(frappe._dict({"name": f"OWN-{book.name}", "book": book.name, "selling_price": None}))
    return items


@frappe.whitelist(allow_guest=True)
def get_store_by_profile(profile_id):
    store = frappe.db.get_value(
        "BusinessStore",
        {"business_profile": profile_id, "is_published": 1},
        ["name", "slug", "store_name", "description"],
        as_dict=True,
    )
    if not store:
        return None
    store.url = f"/store/{store.slug}"
    return store


@frappe.whitelist()
def create_or_update_store(profile_id, store_name, slug, description=None, contact_phone=None, contact_email=None, address=None, city=None, is_published=0):
    existing = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "name")
    if existing:
        doc = frappe.get_doc("BusinessStore", existing)
    else:
        doc = frappe.get_doc({"doctype": "BusinessStore", "business_profile": profile_id})
    doc.store_name = store_name
    doc.slug = slug
    doc.description = description
    doc.contact_phone = contact_phone
    doc.contact_email = contact_email
    doc.address = address
    doc.city = city
    doc.is_published = is_published
    doc.save()
    return {"name": doc.name, "slug": doc.slug, "store_url": f"/store/{doc.slug}"}


@frappe.whitelist()
def get_my_store(profile_id):
    store = frappe.db.get_value(
        "BusinessStore",
        {"business_profile": profile_id},
        ["name", "store_name", "slug", "description", "contact_phone", "contact_email",
         "address", "city", "is_published"],
        as_dict=True,
    )
    return store


# ─── Marketplace (find stores) ────────────────────────────

@frappe.whitelist(allow_guest=True)
def search_stores(text=None, city=None, profile_type=None, limit=20, offset=0):
    filters = {"is_published": 1}
    if city:
        filters["city"] = ["like", f"%{city}%"]
    stores = frappe.get_all(
        "BusinessStore",
        filters=filters,
        fields=["name", "store_name", "slug", "description", "city", "business_profile"],
        limit=limit,
        start=offset,
        order_by="modified desc",
    )
    for s in stores:
        profile = frappe.db.get_value(
            "BusinessProfile", s.business_profile,
            ["business_name", "profile_type"],
            as_dict=True,
        )
        s.business_name = profile.business_name if profile else ""
        s.profile_type = profile.profile_type if profile else ""
        s.store_url = f"/store/{s.slug}"
        if text:
            match = text.lower() in s.store_name.lower() or text.lower() in (s.business_name or "").lower()
            if profile_type:
                match = match and s.profile_type == profile_type
            if not match:
                stores.remove(s)
    return stores


@frappe.whitelist(allow_guest=True)
def get_filters():
    subjects = frappe.get_all("SubjectMaster", fields=["name", "subject_name", "parent_subject"])
    languages = frappe.get_all("Book", fields=["language"], distinct=1)
    return {
        "subjects": subjects,
        "languages": [l.language for l in languages if l.language],
    }


# ─── Public offers: one book, many sellers ────────────────

def _offer_text_filter(text):
    return {"title": ["like", f"%{text}%"], "author": ["like", f"%{text}%"]}


@frappe.whitelist(allow_guest=True)
def search_offers(text=None, subject=None, book_type=None, language=None, city=None, limit=24, offset=0):
    """Each book once, with every published seller that stocks it.

    Sellers in `city` come first, and books that have a seller in `city`
    are listed before the rest. Books with no published seller are skipped.
    """
    limit = min(int(limit or 24), 100)
    offset = int(offset or 0)
    city = (city or "").strip()

    book_filters = {"global_visibility": 1}
    if book_type:
        book_filters["book_type"] = book_type
    if language:
        book_filters["language"] = language
    or_filters = None
    if text:
        or_filters = _offer_text_filter(text)

    subject_books = None
    if subject:
        subject_books = frappe.get_all(
            "BookSubject", filters={"subject": subject, "parenttype": "Book"}, pluck="parent"
        )
        if not subject_books:
            return {"books": [], "total": 0, "cities": _published_cities()}
        book_filters["name"] = ["in", subject_books]

    books = frappe.get_all(
        "Book",
        filters=book_filters,
        or_filters=or_filters,
        fields=["name", "title", "subtitle", "author", "book_type", "language",
                "set_mrp", "cover_image", "primary_category", "genre", "religious_segment", "publisher"],
        order_by="modified desc",
        limit_page_length=2000,
    )
    if not books:
        return {"books": [], "total": 0, "cities": _published_cities()}

    book_names = [b.name for b in books]
    items = list(frappe.get_all(
        "BusinessCatalogItem",
        filters={"book": ["in", book_names], "is_active": 1},
        fields=["book", "business_profile", "selling_price"],
    ))
    # A publisher's own books are on offer from the publisher, even without a catalog row.
    publisher_names = list({b.publisher for b in books if b.publisher})
    publisher_types = {
        p.name for p in frappe.get_all(
            "BusinessProfile",
            filters={"name": ["in", publisher_names], "profile_type": "Publisher"},
            fields=["name"],
        )
    } if publisher_names else set()
    listed = {(i.book, i.business_profile) for i in items}
    for b in books:
        if b.publisher in publisher_types and (b.name, b.publisher) not in listed:
            items.append(frappe._dict({"book": b.name, "business_profile": b.publisher, "selling_price": None}))
    book_mrp = {b.name: b.set_mrp for b in books}
    profiles = list({i.business_profile for i in items})
    stores = frappe.get_all(
        "BusinessStore",
        filters={"business_profile": ["in", profiles], "is_published": 1},
        fields=["business_profile", "slug", "store_name", "city"],
    ) if profiles else []
    store_by_profile = {s.business_profile: s for s in stores}
    profile_rows = frappe.get_all(
        "BusinessProfile",
        filters={"name": ["in", list(store_by_profile)]} if store_by_profile else {"name": ""},
        fields=["name", "business_name", "city", "profile_type"],
    )
    profile_by_name = {p.name: p for p in profile_rows}

    offers_by_book = {}
    for item in items:
        store = store_by_profile.get(item.business_profile)
        if not store:
            continue
        profile = profile_by_name.get(item.business_profile)
        seller_city = store.city or (profile.city if profile else "") or ""
        offers_by_book.setdefault(item.book, []).append({
            "business_profile": item.business_profile,
            "business_name": profile.business_name if profile else store.store_name,
            "store_name": store.store_name,
            "slug": store.slug,
            "profile_type": profile.profile_type if profile else "",
            "city": seller_city,
            "selling_price": item.selling_price if item.selling_price is not None else book_mrp.get(item.book),
            "in_city": bool(city) and seller_city.strip().lower() == city.lower(),
        })

    result = []
    for book in books:
        sellers = offers_by_book.get(book.name)
        if not sellers:
            continue
        sellers.sort(key=lambda s: (not s["in_city"], s["selling_price"] is None, s["selling_price"] or 0))
        prices = [s["selling_price"] for s in sellers if s["selling_price"] is not None]
        result.append({
            **book,
            "sellers": sellers,
            "seller_count": len(sellers),
            "in_city_count": sum(1 for s in sellers if s["in_city"]),
            "min_price": min(prices) if prices else None,
        })

    if city:
        result.sort(key=lambda b: b["in_city_count"] == 0)

    total = len(result)
    return {"books": result[offset:offset + limit], "total": total, "cities": _published_cities()}


def _published_cities():
    rows = frappe.get_all("BusinessStore", filters={"is_published": 1}, fields=["city"], pluck="city")
    return sorted({c.strip() for c in rows if c and c.strip()})
