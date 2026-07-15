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
        fields=["name", "title", "author", "publisher", "book_type", "isbn", "language", "set_mrp", "creation"],
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
    is_multi = book.book_type == "Multi Volume"
    loose_volumes = [v for v in volumes if v.loose_sale] if is_multi else []
    stock_summary = get_stock_summary(book_id, volumes)
    return {
        "book": book.as_dict(),
        "subjects": [s.as_dict() for s in subjects],
        "volumes": [v.as_dict() for v in volumes] if is_multi else [],
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
def add_to_catalog(profile_id, book_id, selling_price=None):
    if frappe.db.exists("BusinessCatalogItem", {"business_profile": profile_id, "book": book_id}):
        return {"error": "Book already in catalog"}
    doc = frappe.get_doc({
        "doctype": "BusinessCatalogItem",
        "business_profile": profile_id,
        "book": book_id,
        "selling_price": selling_price,
        "is_active": 1,
    })
    doc.insert()
    return {"name": doc.name, "book": book_id, "status": "added"}


@frappe.whitelist()
def remove_from_catalog(item_id):
    frappe.delete_doc("BusinessCatalogItem", item_id)
    return {"status": "removed"}


@frappe.whitelist()
def list_catalog(profile_id, active_only=True):
    filters = {"business_profile": profile_id}
    if active_only:
        filters["is_active"] = 1
    items = frappe.get_all(
        "BusinessCatalogItem",
        filters=filters,
        fields=["name", "book", "selling_price", "is_active", "added_on"],
        order_by="added_on desc",
    )
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            ["title", "author", "book_type", "set_mrp", "language"],
            as_dict=True,
        )
        item.book_title = book.title if book else ""
        item.book_author = book.author if book else ""
        item.book_type = book.book_type if book else ""
        item.book_mrp = book.set_mrp if book else None
        stock = frappe.db.get_value(
            "StockLedger",
            {"book": item.book, "business_profile": profile_id, "state": "Available"},
            "sum(quantity)",
        )
        item.stock_available = stock or 0
    return items


@frappe.whitelist()
def update_catalog_price(item_id, selling_price):
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    doc.selling_price = selling_price
    doc.save()
    return {"name": doc.name, "selling_price": selling_price}


@frappe.whitelist()
def toggle_catalog_item(item_id, is_active):
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    doc.is_active = is_active
    doc.save()
    return {"name": doc.name, "is_active": is_active}


# ─── Business Store (public storefront) ───────────────────

@frappe.whitelist(allow_guest=True)
def get_store_by_slug(slug):
    store = frappe.db.get_value(
        "BusinessStore",
        {"slug": slug, "is_published": 1},
        ["name", "business_profile", "store_name", "description", "banner_image",
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
    items = frappe.get_all(
        "BusinessCatalogItem",
        filters={"business_profile": store.business_profile, "is_active": 1},
        fields=["name", "book", "selling_price"],
        order_by="modified desc",
    )
    catalog = []
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            ["title", "author", "book_type", "set_mrp", "language", "binding"],
            as_dict=True,
        )
        stock = frappe.db.get_value(
            "StockLedger",
            {"book": item.book, "business_profile": store.business_profile, "state": "Available"},
            "sum(quantity)",
        )
        catalog.append({
            "item_name": item.name,
            "book_id": item.book,
            "title": book.title if book else "",
            "author": book.author if book else "",
            "book_type": book.book_type if book else "",
            "mrp": book.set_mrp if book else None,
            "selling_price": item.selling_price,
            "language": book.language if book else "",
            "stock": stock or 0,
        })
    store.catalog = catalog
    store.catalog_count = len(catalog)
    return store


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
