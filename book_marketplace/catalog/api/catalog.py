import re
import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def search(text=None, subject=None, publisher=None, author=None, language=None, board=None, book_type=None, limit=20, offset=0,
           item_type=None):
    filters = {"global_visibility": 1}
    if item_type:
        filters["item_type"] = item_type
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
            "name", "title", "title_en", "title_ur", "subtitle", "author", "author_en", "author_profile", "publisher",
            "book_type", "isbn", "language", "set_mrp", "primary_category",
            "genre", "religious_segment", "introduction", "creation", "item_type"
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
    if not book_id or not frappe.db.exists("Book", book_id):
        return None  # unknown id: the store shows Book not found, not a permission error
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
        "price": price_view(book.set_mrp, publisher_discount(book=book.as_dict())),
        "images": [{"image": i.image, "caption": i.caption or ""} for i in book.get("images", []) if i.image],
        **_related_books(book),
    }


def _core_title(title):
    """The text's own name: no brackets, no «مع شرح ...», no edition words (عربی، محشی، مجلد)."""
    t = re.sub(r"[\u064B-\u065F\u0670]", "", title or "")
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.split(r"\s+مع\s+", t)[0]
    for a, b in (("ۂ", "ہ"), ("ۃ", "ہ"), ("ة", "ہ"), ("ي", "ی"), ("ك", "ک"), ("أ", "ا"), ("إ", "ا"), ("آ", "ا")):
        t = t.replace(a, b)
    t = re.sub(r"\b(عربی|اردو|محشی|مجلد|درسی|کامل|مکمل)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _related_books(book):
    """For the book page: the text it explains, its commentaries (شروحات), and the same text from other publishers."""
    fields = ["name", "title", "author", "language", "set_mrp", "publisher", "cover_image"]

    def with_publisher(rows):
        names = {r.publisher for r in rows if r.publisher}
        pub = {p.name: p.business_name for p in frappe.get_all(
            "BusinessProfile", filters={"name": ["in", list(names)]}, fields=["name", "business_name"])} if names else {}
        listed = set(frappe.get_all("BusinessCatalogItem", filters={"book": ["in", [r.name for r in rows]], "is_active": 1},
                                    pluck="book")) if rows else set()
        return [{**r, "publisher_name": pub.get(r.publisher, ""), "listed": r.name in listed} for r in rows]

    text = None
    if book.get("sharh_of") and frappe.db.exists("Book", book.sharh_of):
        text = with_publisher([frappe.db.get_value("Book", book.sharh_of, fields, as_dict=True)])[0]
    commentaries = with_publisher(frappe.get_all("Book", filters={"sharh_of": book.name, "global_visibility": 1},
                                                 fields=fields, order_by="title asc", limit_page_length=100))
    core = _core_title(book.title)
    editions = []
    if len(core) >= 3:
        index = frappe.cache().get_value("bm_core_title_index")
        if not index:
            index = {}
            for r in frappe.get_all("Book", filters={"global_visibility": 1}, fields=["name", "title"], limit_page_length=0):
                index.setdefault(_core_title(r.title), []).append(r.name)
            frappe.cache().set_value("bm_core_title_index", index, expires_in_sec=600)
        same = [n for n in index.get(core, []) if n != book.name]
        if same:
            editions = with_publisher(frappe.get_all("Book", filters={"name": ["in", same]}, fields=fields,
                                                     order_by="title asc", limit_page_length=50))
    shown = {book.name} | {c["name"] for c in commentaries} | {e["name"] for e in editions}
    if text:
        shown.add(text["name"])
    related, reason = _recommend(book, shown, fields, with_publisher)
    from_publisher = []
    if book.publisher:
        rows = frappe.get_all("Book", filters={"publisher": book.publisher, "global_visibility": 1,
                                               "name": ["not in", list(shown)]},
                              fields=fields, limit_page_length=60)
        from_publisher = sorted(with_publisher(rows), key=lambda r: (not r["listed"], r["title"]))[:8]
    holder = [{"name": book.name}]
    _attach_classifications(holder)
    # more books from each published shop that lists this book (shops other than the publisher's own first)
    from_shops = []
    shop_ids = list(dict.fromkeys(frappe.get_all(
        "BusinessCatalogItem", filters={"book": book.name, "is_active": 1}, pluck="business_profile")))
    stores = {st.business_profile: st for st in frappe.get_all(
        "BusinessStore", filters={"business_profile": ["in", shop_ids or [""]], "is_published": 1},
        fields=["business_profile", "slug", "store_name", "city"])}
    for prof in sorted((x for x in shop_ids if x in stores), key=lambda x: x == book.publisher)[:3]:
        names = [n for n in dict.fromkeys(frappe.get_all(
            "BusinessCatalogItem", filters={"business_profile": prof, "is_active": 1, "book": ["!=", book.name]},
            pluck="book", limit_page_length=200))]
        if not names:
            continue
        rows = frappe.get_all("Book", filters={"name": ["in", names], "global_visibility": 1}, fields=fields,
                              limit_page_length=60)
        # the shop's books closest to this one first: the most classifications in common, then by title
        mine = frappe.get_all("BookClassification", filters={"parent": book.name, "parenttype": "Book"}, pluck="category")
        common = {}
        if mine and rows:
            for r in frappe.get_all("BookClassification", filters={"parent": ["in", [x.name for x in rows]],
                                    "parenttype": "Book", "category": ["in", mine]}, fields=["parent"], limit_page_length=3000):
                common[r.parent] = common.get(r.parent, 0) + 1
        rows = sorted(with_publisher(rows), key=lambda r: (-common.get(r["name"], 0), r["title"]))[:8]
        st = stores[prof]
        from_shops.append({"business_profile": prof, "slug": st.slug, "store_name": st.store_name, "city": st.city or "",
                           "total": len(names), "books": rows})
    publisher_label = frappe.db.get_value("BusinessProfile", book.publisher, "business_name") if book.publisher else ""
    return {"text_of": text, "commentaries": commentaries, "editions": editions, "related": related, "related_by": reason,
            "classifications": holder[0]["classifications"], "from_publisher": from_publisher, "from_shops": from_shops,
            "shelves": _shelves(book, fields, with_publisher, from_shops),
            "publisher_label": publisher_label or ""}


def _shelves(book, fields, with_publisher, from_shops):
    """The book page's rows. Each row holds only one kind of book:
    same darja (exactly that darja), same publisher, each shop, same subject, same broader department, same title type.
    Commentaries are left out of the classification rows: they show under their text."""
    from book_marketplace.catalog.api.classification import descendants
    own = frappe.get_all("BookClassification", filters={"parent": book.name, "parenttype": "Book"}, pluck="category")
    cats = frappe.get_all("Book Category", filters={"name": ["in", own or [""]]},
                          fields=["name", "category_type", "category_name_ur", "category_name", "is_group", "parent_book_category"])
    lab = lambda c: c.category_name_ur or c.category_name

    def books_in(names, with_sharh=False, extra_filters=None):
        if not names:
            return 0, []
        filt = {"name": ["in", names], "global_visibility": 1, "name": ["not in", [book.name]]}
        rows = frappe.get_all("Book", filters={"name": ["in", names], "global_visibility": 1}, fields=fields + ["sharh_of"],
                              limit_page_length=300)
        rows = [r for r in rows if r.name != book.name and (with_sharh or not r.get("sharh_of"))]
        out = sorted(with_publisher(rows), key=lambda r: (not r["listed"], r["title"]))
        return len(out), out[:12]

    def members(cat_names):
        return list({r for r in frappe.get_all("BookClassification", filters={"category": ["in", cat_names], "parenttype": "Book"},
                                               pluck="parent", limit_page_length=3000)})

    shelves = []
    # 1. same darja: the book's leaf departments, exactly
    for c in [x for x in cats if x.category_type == "Department" and not x.is_group]:
        total, rows = books_in(members([c.name]))
        if rows:
            shelves.append({"kind": "darja", "label": f"اسی درجے کی کتابیں: {lab(c)}", "ref": c.name, "ref_type": "Department",
                            "total": total, "books": rows})
    # 2. same publisher (everything of the publisher)
    if book.publisher:
        names = frappe.get_all("Book", filters={"publisher": book.publisher, "name": ["!=", book.name]}, pluck="name", limit_page_length=500)
        total, rows = books_in(names, with_sharh=True)
        if rows:
            label = frappe.db.get_value("BusinessProfile", book.publisher, "business_name") or ""
            shelves.append({"kind": "publisher", "label": f"اسی ناشر کی کتابیں: {label}".strip(": "), "ref": book.publisher,
                            "ref_type": "Publisher", "total": total, "books": rows})
    # 3. each shop that lists this book
    for sh in from_shops:
        shelves.append({"kind": "shop", "label": f"اسی دکان سے: {sh['store_name']}", "ref": sh["slug"], "ref_type": "Shop",
                        "total": sh["total"], "books": sh["books"]})
    # 4. same subject (leaf subjects, up to two)
    for c in [x for x in cats if x.category_type == "Subject" and not x.is_group][:2]:
        total, rows = books_in(members([c.name]))
        if rows:
            shelves.append({"kind": "subject", "label": f"اسی موضوع کی کتابیں: {lab(c)}", "ref": c.name, "ref_type": "Subject",
                            "total": total, "books": rows})
    # 5. same broader department (the stage above the darja), without the darja's own books
    for c in [x for x in cats if x.category_type == "Department" and not x.is_group]:
        parent = c.parent_book_category
        if not parent:
            continue
        pc = frappe.db.get_value("Book Category", parent, ["category_name", "category_name_ur", "parent_book_category"], as_dict=True)
        if not pc or not pc.parent_book_category:
            continue
        mine = set(members([c.name]))
        pool = [n for n in members(list(descendants([parent]))) if n not in mine]
        total, rows = books_in(pool)
        if rows:
            shelves.append({"kind": "department", "label": f"اسی شعبے کی دوسری کتابیں: {pc.category_name_ur or pc.category_name}",
                            "ref": parent, "ref_type": "Department", "total": total, "books": rows})
    # 6. same title type
    for c in [x for x in cats if x.category_type == "Title Type"]:
        total, rows = books_in(members([c.name]))
        if rows:
            shelves.append({"kind": "form", "label": f"اسی قسم کی کتابیں: {lab(c)}", "ref": c.name, "ref_type": "Title Type",
                            "total": total, "books": rows})
    return shelves


# weights of the recommendation signals (higher = closer)
RECO_WEIGHTS = {"darja": 5, "subject": 4, "publisher": 3, "shop": 2, "department": 2, "form": 1, "listed": 1, "commentary": -1}


def _recommend(book, shown, fields, with_publisher, limit=12):
    """Related books by score. Signals: same darja or specific department, same specific subject, same publisher,
    sold by the same shop, same broader department, same title type; a shop listing adds a little, a commentary of
    another text loses a little. Each result carries the reasons that matched."""
    own = frappe.get_all("BookClassification", filters={"parent": book.name, "parenttype": "Book"}, pluck="category")
    cats = {c.name: c for c in frappe.get_all(
        "Book Category", filters={"name": ["in", own or [""]]},
        fields=["name", "category_type", "category_name_ur", "category_name", "is_group"])}
    score, reasons = {}, {}

    def add(name, key, label):
        if name in shown:
            return
        score[name] = score.get(name, 0) + RECO_WEIGHTS[key]
        if label:
            reasons.setdefault(name, []).append(label)

    if cats:
        for r in frappe.get_all("BookClassification", filters={"category": ["in", list(cats)], "parenttype": "Book"},
                                fields=["parent", "category"], limit_page_length=3000):
            c = cats[r.category]
            lab = c.category_name_ur or c.category_name
            if c.category_type == "Department":
                add(r.parent, "department" if c.is_group else "darja", lab)
            elif c.category_type == "Subject":
                add(r.parent, "department" if c.is_group else "subject", lab)
            elif c.category_type == "Title Type":
                add(r.parent, "form", None)
    if book.publisher:
        pub_label = frappe.db.get_value("BusinessProfile", book.publisher, "business_name") or ""
        for n in frappe.get_all("Book", filters={"publisher": book.publisher}, pluck="name", limit_page_length=500):
            add(n, "publisher", f"اسی ناشر: {pub_label}" if pub_label else "اسی ناشر")
    shops = frappe.get_all("BusinessCatalogItem", filters={"book": book.name, "is_active": 1}, pluck="business_profile")
    if shops:
        shop_names = {p.name: p.business_name for p in frappe.get_all(
            "BusinessProfile", filters={"name": ["in", shops]}, fields=["name", "business_name"])}
        seen = {}
        for r in frappe.get_all("BusinessCatalogItem", filters={"business_profile": ["in", shops], "is_active": 1},
                                fields=["book", "business_profile"], limit_page_length=2000):
            seen.setdefault(r.book, set()).add(r.business_profile)
        for n, profs in seen.items():
            for prof in list(profs)[:2]:
                add(n, "shop", f"اسی دکان پر: {shop_names.get(prof, '')}")
    if not score:
        return [], ""
    top = sorted(score, key=lambda n: -score[n])[:80]
    rows = {r.name: r for r in frappe.get_all("Book", filters={"name": ["in", top], "global_visibility": 1},
                                             fields=fields + ["sharh_of"], limit_page_length=80)}
    listed = set(frappe.get_all("BusinessCatalogItem", filters={"book": ["in", list(rows) or [""]], "is_active": 1},
                                pluck="book"))
    for n, r in rows.items():
        if n in listed:
            score[n] += RECO_WEIGHTS["listed"]
        if r.get("sharh_of"):
            score[n] += RECO_WEIGHTS["commentary"]
    best = sorted(rows, key=lambda n: (-score[n], rows[n].title))[:limit]
    out = with_publisher([rows[n] for n in best])
    for r in out:
        # keep each reason once, most informative first
        seen_r = []
        for x in reasons.get(r["name"], []):
            if x not in seen_r:
                seen_r.append(x)
        r["reasons"] = seen_r[:3]
        r["score"] = score[r["name"]]
    top_reason = out[0]["reasons"][0] if out and out[0]["reasons"] else ""
    return out, top_reason


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

STORAGE_PLACES = ("", "Shop", "Warehouse")


def _pct(value):
    """Discount percent as a float between 0 and 100. Blank is 0."""
    v = frappe.utils.flt(value)
    return min(max(v, 0), 100)


def price_view(list_price, discount_percent):
    """General price, discount and the price after discount.
    list_price 0 or blank means no price is known."""
    lp = frappe.utils.flt(list_price) or None
    d = _pct(discount_percent) if lp else 0
    net = round(lp * (100 - d) / 100) if lp else None
    return {"list_price": lp, "discount_percent": d, "net_price": net}


def _publisher_of(book_id):
    return frappe.db.get_value("Book", book_id, "publisher") or "" if book_id else ""


def publisher_discount(book_id=None, book=None, _cache=None):
    """The publisher's discount for one book: the book's own rate when set, else the publisher's general rate."""
    if book is None:
        book = frappe.db.get_value("Book", book_id, ["publisher", "own_discount", "discount_percent"], as_dict=True) or frappe._dict()
    if frappe.utils.cint(book.get("own_discount")):
        return _pct(book.get("discount_percent"))
    pub = book.get("publisher")
    if not pub:
        return 0
    if _cache is not None:
        if pub not in _cache:
            _cache[pub] = _pct(frappe.db.get_value("BusinessProfile", pub, "default_discount_percent"))
        return _cache[pub]
    return _pct(frappe.db.get_value("BusinessProfile", pub, "default_discount_percent"))


def publisher_list_price(book_id, publisher, set_mrp):
    """The publisher's own general price, as its list sets it: its catalog row's price, else the book's MRP."""
    row_price = frappe.db.get_value("BusinessCatalogItem", {"business_profile": publisher, "book": book_id},
                                    "selling_price") if book_id and publisher else None
    return frappe.utils.flt(row_price) or frappe.utils.flt(set_mrp)


def shop_general_price(profile_id, book_publisher, set_mrp, stored_price, book_id=None):
    """The general (public) price a business shows for a book it holds.
    A book from another publisher follows that publisher's own list price at once (its catalog row, else the MRP);
    the shop only sets its own discount. A publisher's own book, and anything without a publisher, keeps its stored price."""
    if book_publisher and book_publisher != profile_id:
        pub_price = publisher_list_price(book_id, book_publisher, set_mrp) if book_id else frappe.utils.flt(set_mrp)
        if pub_price:
            return pub_price
    return frappe.utils.flt(stored_price) or frappe.utils.flt(set_mrp)


def _catalog_row(profile_id, book_id, selling_price=None, trade_price=None, discount_percent=None,
                 shelf=None, storage_place=None):
    if storage_place not in STORAGE_PLACES:
        frappe.throw(_("Place must be Shop or Warehouse."))
    return {
        "doctype": "BusinessCatalogItem",
        "business_profile": profile_id,
        "book": book_id,
        # a book from another publisher keeps no price of its own: the publisher's price is read when shown
        "selling_price": None if _publisher_of(book_id) not in ("", profile_id) else (frappe.utils.flt(selling_price) or None),
        "trade_price": frappe.utils.flt(trade_price) or None,
        "discount_percent": _pct(discount_percent),
        "shelf": (shelf or "").strip() or None,
        "storage_place": storage_place or None,
        "is_active": 1,
    }


@frappe.whitelist()
def add_to_catalog(profile_id, book_id, selling_price=None, trade_price=None, discount_percent=None,
                   shelf=None, storage_place=None):
    from book_marketplace.auth.api.auth import _assert_profile_owner

    _assert_profile_owner(profile_id)
    if frappe.db.exists("BusinessCatalogItem", {"business_profile": profile_id, "book": book_id}):
        return {"error": "Book already in catalog"}
    doc = frappe.get_doc(_catalog_row(profile_id, book_id, selling_price, trade_price, discount_percent,
                                      shelf, storage_place))
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "book": book_id, "status": "added"}


@frappe.whitelist()
def add_many_to_catalog(profile_id, items):
    """A shop adds several books at once, each with its own price, discount, shelf and place.
    items: list of {book, selling_price, trade_price, discount_percent, shelf, storage_place}.
    Books already in the catalog are skipped."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    _assert_profile_owner(profile_id)
    if isinstance(items, str):
        items = frappe.parse_json(items)
    added, skipped = [], []
    for it in items or []:
        book_id = it.get("book")
        if not book_id or not frappe.db.exists("Book", book_id):
            skipped.append({"book": book_id, "reason": "not found"})
            continue
        if frappe.db.exists("BusinessCatalogItem", {"business_profile": profile_id, "book": book_id}):
            skipped.append({"book": book_id, "reason": "already in catalog"})
            continue
        doc = frappe.get_doc(_catalog_row(profile_id, book_id, it.get("selling_price"), it.get("trade_price"),
                                          it.get("discount_percent"), it.get("shelf"), it.get("storage_place")))
        doc.insert(ignore_permissions=True)
        added.append(book_id)
    return {"added": len(added), "skipped": skipped}


@frappe.whitelist()
def list_publishers():
    """Every approved publisher, with the count of its visible books (0 if none yet) and the general discount.
    The shop uses the stock filter on the order form to narrow books to those it can get now."""
    pubs = frappe.get_all("BusinessProfile", filters={"profile_type": "Publisher", "status": "Approved"},
                          fields=["name", "business_name", "city", "default_discount_percent"],
                          order_by="business_name asc")
    counts = dict(frappe.db.sql("""select publisher, count(*) from `tabBook`
        where global_visibility = 1 and ifnull(publisher, '') != '' group by publisher"""))
    out = []
    for p in pubs:
        n = counts.get(p.name, 0)
        out.append({"name": p.name, "business_name": p.business_name, "city": p.city,
                    "default_discount_percent": _pct(p.default_discount_percent), "books": n})
    return out


@frappe.whitelist()
def publisher_books(profile_id, publishers, text=None, limit=50, offset=0, available_only=0):
    """Books of one or more publishers, for a shop to tick and add.
    Each row has the publisher's general price, its discount and trade price, and whether the shop already has it."""
    if isinstance(publishers, str):
        publishers = frappe.parse_json(publishers) if publishers.strip().startswith("[") else [publishers]
    publishers = [p for p in (publishers or []) if p]
    if not publishers:
        return {"books": [], "total": 0}
    limit = min(frappe.utils.cint(limit) or 50, 200)
    offset = frappe.utils.cint(offset)
    # Every visible book of the chosen publishers (the publisher's own catalog rows are not needed).
    filters = {"publisher": ["in", publishers], "global_visibility": 1}
    if frappe.utils.cint(available_only):
        # only the books this shop itself holds in stock (Available, quantity above zero)
        held = {}
        for row in frappe.get_all("StockLedger", filters={"business_profile": profile_id, "state": "Available"},
                                  fields=["book", "quantity"], limit_page_length=0):
            held[row.book] = held.get(row.book, 0) + float(row.quantity or 0)
        in_stock = [b for b, q in held.items() if q > 0]
        filters["name"] = ["in", in_stock or [""]]
    or_filters = _offer_text_filter(text) if text else None
    total = len(frappe.get_all("Book", filters=filters, or_filters=or_filters, pluck="name", limit_page_length=0))
    books = frappe.get_all(
        "Book", filters=filters, or_filters=or_filters,
        fields=["name", "title", "author", "publisher", "set_mrp", "trade_price", "own_discount",
                "discount_percent", "availability", "language", "binding", "is_discontinued"],
        order_by="title asc", limit_start=offset, limit_page_length=limit,
    )
    have = set(frappe.get_all("BusinessCatalogItem", filters={"business_profile": profile_id,
                                                              "book": ["in", [b.name for b in books] or [""]]},
                              pluck="book"))
    names = {p.name: p.business_name for p in frappe.get_all(
        "BusinessProfile", filters={"name": ["in", publishers]}, fields=["name", "business_name"])}
    cache = {}
    # The price shown is the publisher's own price (the one its public store shows): its catalog row's price,
    # discount and trade price, else the book's MRP and the publisher's discount.
    own_rows = {(r.business_profile, r.book): r for r in frappe.get_all(
        "BusinessCatalogItem",
        filters={"business_profile": ["in", publishers], "book": ["in", [b.name for b in books] or [""]]},
        fields=["business_profile", "book", "selling_price", "discount_percent", "trade_price"], limit_page_length=0)}
    rows = []
    for b in books:
        own = own_rows.get((b.publisher, b.name))
        list_price = (own.selling_price if own else None) or b.set_mrp
        d = (own.discount_percent if own else None) or publisher_discount(book=b, _cache=cache)
        trade = (own.trade_price if own else None) or b.trade_price
        rows.append({
            "name": b.name, "title": b.title, "author": b.author, "publisher": b.publisher,
            "publisher_name": names.get(b.publisher, ""), "language": b.language, "binding": b.binding,
            "availability": b.availability or "Available",
            "set_mrp": list_price, "trade_price": trade,
            "publisher_discount": d, "net_price": price_view(list_price, d)["net_price"],
            "is_discontinued": frappe.utils.cint(b.is_discontinued),
            "in_catalog": b.name in have,
        })
    return {"books": rows, "total": total}


@frappe.whitelist()
def set_publisher_discount(profile_id, discount_percent):
    """Publisher's general discount: used for every book that has no discount of its own."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    _assert_profile_owner(profile_id)
    if frappe.db.get_value("BusinessProfile", profile_id, "profile_type") != "Publisher":
        frappe.throw(_("Only a publisher has a general discount."))
    before = _pct(frappe.db.get_value("BusinessProfile", profile_id, "default_discount_percent"))
    frappe.db.set_value("BusinessProfile", profile_id, "default_discount_percent", _pct(discount_percent))
    from book_marketplace.catalog.api.price_changes import on_general_discount_change
    on_general_discount_change(profile_id, before, _pct(discount_percent))
    return {"name": profile_id, "default_discount_percent": _pct(discount_percent)}


@frappe.whitelist()
def get_publisher_discount(profile_id):
    return {"default_discount_percent": _pct(frappe.db.get_value("BusinessProfile", profile_id, "default_discount_percent"))}


@frappe.whitelist()
def remove_from_catalog(item_id):
    """Takes a book off the profile's list. The book itself stays in the publisher's books."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    if str(item_id).startswith("OWN-"):
        frappe.throw(_("This book is not in your list."))
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    _assert_profile_owner(doc.business_profile)
    frappe.delete_doc("BusinessCatalogItem", item_id, ignore_permissions=True)
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
        fields=["name", "book", "selling_price", "trade_price", "is_active", "added_on",
                "discount_percent", "shelf", "storage_place", "is_advertised"],
        order_by="added_on desc",
    )
    rows, listed, cache = [], set(), {}
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            ["title", "author", "book_type", "set_mrp", "trade_price", "language", "genre", "religious_segment",
             "publisher", "own_discount", "discount_percent", "is_discontinued", "item_type"],
            as_dict=True,
        ) or frappe._dict()
        listed.add(item.book)
        rows.append({
            "name": item.name,
            "book": item.book,
            "book_title": book.title or "",
            "book_author": book.author or "",
            "book_type": book.book_type or "",
            "item_type": book.get("item_type") or "Book",
            "book_mrp": book.set_mrp,
            "book_trade_price": book.trade_price,
            "language": book.language or "",
            "title_ur": book.get("title_ur") or "",
            "genre": book.genre or "",
            "religious_segment": book.religious_segment or "",
            "selling_price": shop_general_price(profile_id, book.get("publisher"), book.get("set_mrp"), item.selling_price, item.book)
            if book.get("publisher") != profile_id else (item.selling_price or book.get("set_mrp")),
            "trade_price": item.trade_price or None,
            "discount_percent": _pct(item.discount_percent) or (publisher_discount(book_id=item.book) if book.get("publisher") == profile_id else 0),
            "publisher_discount": publisher_discount(book=book, _cache=cache) if book else 0,
            "shelf": item.shelf or "",
            "is_advertised": bool(item.is_advertised),
            "storage_place": item.storage_place or "",
            "is_active": item.is_active,
            "added_on": item.added_on,
            "owned": 1 if book.get("publisher") == profile_id else 0,
            "from_publisher": book.get("publisher") or "",
            "is_discontinued": frappe.utils.cint(book.get("is_discontinued")),
            "stock_available": _stock_for(item.book, profile_id),
        })
    return rows


@frappe.whitelist()
def update_catalog_price(item_id, selling_price=None, trade_price=None, profile_id=None,
                         discount_percent=None, shelf=None, storage_place=None):
    """Prices of one catalog row. For a publisher's own book (OWN-<book>), discount_percent "" means
    "use my general discount"; a number sets the book's own discount.
    For a shop row, discount_percent is the shop's own discount; shelf and place say where the copy is kept."""
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
        if discount_percent is not None:
            if str(discount_percent).strip() == "":
                book.own_discount = 0
                book.discount_percent = 0
            else:
                book.own_discount = 1
                book.discount_percent = _pct(discount_percent)
        book.save(ignore_permissions=True)
        return {"name": item_id, "selling_price": book.set_mrp, "trade_price": book.trade_price,
                "discount_percent": publisher_discount(book=book.as_dict())}

    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    _assert_profile_owner(doc.business_profile)
    if selling_price is not None:
        if _publisher_of(doc.book) not in ("", doc.business_profile):
            frappe.throw(_("This book's price is the publisher's price. Change your discount instead."))
        doc.selling_price = selling_price
    if trade_price is not None:
        doc.trade_price = trade_price
    if discount_percent is not None:
        doc.discount_percent = _pct(discount_percent)
    if shelf is not None:
        doc.shelf = shelf.strip() or None
    if storage_place is not None:
        if storage_place not in STORAGE_PLACES:
            frappe.throw(_("Place must be Shop or Warehouse."))
        doc.storage_place = storage_place or None
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "selling_price": doc.selling_price, "trade_price": doc.trade_price,
            "discount_percent": doc.discount_percent, "shelf": doc.shelf, "storage_place": doc.storage_place}


@frappe.whitelist()
def toggle_catalog_item(item_id, is_active):
    doc = frappe.get_doc("BusinessCatalogItem", item_id)
    doc.is_active = is_active
    doc.save()
    return {"name": doc.name, "is_active": is_active}


AVAILABILITY_BLOCKS_DEALERS = {"Unavailable", "Out of print", "Forthcoming", "General readers only"}


@frappe.whitelist()
def set_book_availability(profile_id, book_id, availability):
    """Publisher marks its own book. Shops cannot change this."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    book = frappe.get_doc("Book", book_id)
    if book.publisher != profile_id and book.get("owner_profile") != profile_id:
        frappe.throw(_("Only the publisher or the bookseller who added this book can change its availability."), frappe.PermissionError)
    _assert_profile_owner(profile_id)
    allowed = ["Available", "Unavailable", "Out of print", "Forthcoming", "General readers only"]
    if availability not in allowed:
        frappe.throw(_("Unknown availability."))
    book.availability = availability
    book.save(ignore_permissions=True)
    return {"name": book.name, "availability": availability}


@frappe.whitelist()
def set_book_images(profile_id, book_id, images):
    """Publisher sets the extra images of its own book. images: JSON list of {image, caption}.
    The first image in the list becomes the cover only if the book has none."""
    import json as _json
    from book_marketplace.auth.api.auth import _assert_profile_owner

    book = frappe.get_doc("Book", book_id)
    if book.publisher != profile_id and book.get("owner_profile") != profile_id:
        frappe.throw(_("Only the publisher or the bookseller who added this book can change its images."), frappe.PermissionError)
    _assert_profile_owner(profile_id)
    items = _json.loads(images) if isinstance(images, str) else (images or [])
    if len(items) > 12:
        frappe.throw(_("Up to 12 images per book."))
    book.set("images", [])
    for it in items:
        url = (it.get("image") or "").strip()
        if url:
            book.append("images", {"image": url, "caption": (it.get("caption") or "").strip()[:140]})
    book.save(ignore_permissions=True)
    return {"name": book.name, "count": len(book.images)}


@frappe.whitelist()
def add_shop_book(profile_id, title, author=None, book_type="Single Volume", language="Urdu", set_mrp=None,
                  selling_price=None, binding=None, isbn=None, quantity=0, item_type="Book"):
    """Any business profile adds a book it holds (old, out of print, or one it printed itself). It is listed in its own shop.
    The publisher list goes through create_own_book with the matching rule."""
    from frappe.utils import nowdate
    from book_marketplace.auth.api.auth import _assert_profile_owner
    from book_marketplace.stock_management.api import stock as stock_api

    _assert_profile_owner(profile_id)
    title = (title or "").strip()
    author = (author or "").strip()
    item_type = (item_type or "Book").strip()
    allowed = (frappe.get_meta("Book").get_field("item_type").options or "Book").split("\n")
    if item_type not in allowed:
        frappe.throw(_("Unknown item type."))
    from book_marketplace.auth.api.shop_settings import item_types_of
    if item_type not in item_types_of(profile_id):
        frappe.throw(_("Your shop does not sell {0}. Add it in Settings > What I sell.").format(item_type))
    is_book = item_type == "Book"
    if not title or (is_book and not author):
        frappe.throw(_("Title and author are required.") if is_book else _("Item name is required."))
    if is_book:
        existing = frappe.db.get_value("Book", {"title": title, "author": author}, "name")
        if existing:
            frappe.throw(_("This book is already in the list: {0}. Add it from the list to your shop.").format(existing))
    else:
        # other goods (stationery, gifts, ...) belong to the shop that adds them; the same name twice in one shop is refused
        existing = frappe.db.get_value("Book", {"title": title, "item_type": item_type, "owner_profile": profile_id}, "name")
        if existing:
            frappe.throw(_("This item is already in your shop: {0}.").format(existing))
    is_publisher = frappe.db.get_value("BusinessProfile", profile_id, "profile_type") == "Publisher"
    book = frappe.get_doc({
        "doctype": "Book", "title": title, "item_type": item_type, "author": author or None,
        "book_type": book_type or "Single Volume",
        "language": language or "Urdu", "set_mrp": set_mrp, "binding": binding, "isbn": isbn,
        "publisher": profile_id if is_publisher else None,
        "owner_profile": None if is_publisher else profile_id, "global_visibility": 1,
    }).insert(ignore_permissions=True)
    frappe.get_doc({
        "doctype": "BusinessCatalogItem", "business_profile": profile_id, "book": book.name,
        "selling_price": selling_price or set_mrp, "is_active": 1, "added_on": nowdate(),
    }).insert(ignore_permissions=True)
    if int(quantity or 0) > 0:
        stock_api.set_own_book_quantity(profile_id, book.name, int(quantity))
    return {"name": book.name, "title": title, "status": "created"}


@frappe.whitelist()
def set_book_subjects(profile_id, book_id, subjects=None, page_count=None, cover_image=None):
    """The publisher or the bookseller who added a book fills its details later: subjects (many, JSON list of
    SubjectMaster names), page count and cover picture. Nothing here is required when the book is added."""
    import json as _json
    from book_marketplace.auth.api.auth import _assert_profile_owner

    book = frappe.get_doc("Book", book_id)
    if book.publisher != profile_id and book.get("owner_profile") != profile_id:
        frappe.throw(_("Only the publisher or the bookseller who added this book can change its details."),
                     frappe.PermissionError)
    _assert_profile_owner(profile_id)
    names = _json.loads(subjects) if isinstance(subjects, str) else (subjects or [])
    names = [n for n in dict.fromkeys(names) if n]
    for n in names:
        if not frappe.db.exists("SubjectMaster", n):
            frappe.throw(_("Unknown subject: {0}").format(n))
    book.set("subjects", [])
    for n in names:
        book.append("subjects", {"subject": n})
    if names:
        book.primary_category = names[0]
    if page_count not in (None, ""):
        book.page_count = int(page_count)
    if cover_image:
        book.cover_image = cover_image
    book.save(ignore_permissions=True)
    return {"name": book.name, "subjects": names, "page_count": book.page_count, "cover_image": book.cover_image or ""}


def _owns_book(book, profile_id):
    return profile_id in (book.get("publisher"), book.get("owner_profile"))


@frappe.whitelist()
def own_shop_books():
    """Book names the signed-in account holds itself: in its own shop catalogue, or published/added by its business profiles."""
    if not frappe.session.user or frappe.session.user == "Guest":
        return []
    profiles = frappe.get_all("BusinessProfile", filters={"user": frappe.session.user}, pluck="name", limit_page_length=0)
    if not profiles:
        return []
    names = set(frappe.get_all("BusinessCatalogItem", filters={"business_profile": ["in", profiles]}, pluck="book", limit_page_length=0))
    names |= set(frappe.get_all("Book", filters={"publisher": ["in", profiles]}, pluck="name", limit_page_length=0))
    names |= set(frappe.get_all("Book", filters={"owner_profile": ["in", profiles]}, pluck="name", limit_page_length=0))
    return sorted(n for n in names if n)


@frappe.whitelist()
def my_books(profile_id, text=""):
    """Books this business owns: the ones it published and the ones it added itself. Each row says which."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)
    filters = [["publisher", "=", profile_id]]
    rows = frappe.get_all("Book", or_filters=[["publisher", "=", profile_id], ["owner_profile", "=", profile_id]],
                          fields=["name", "title", "author", "publisher", "owner_profile", "cover_image", "page_count", "item_type"],
                          order_by="title asc", limit_page_length=1000)
    t = (text or "").strip().lower()
    out = []
    for r in rows:
        if t and t not in (r.title or "").lower() and t not in (r.author or "").lower():
            continue
        out.append({"name": r.name, "title": r.title, "author": r.author or "",
                    "role": "publisher" if r.publisher == profile_id else "owner",
                    "cover_image": r.cover_image or "", "page_count": r.page_count or 0,
                    "item_type": r.item_type or "Book"})
    return out


@frappe.whitelist()
def update_book_details(profile_id, book_id, data):
    """The publisher or the bookseller who added the book edits its details. Other businesses cannot."""
    import json as _json
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)
    book = frappe.get_doc("Book", book_id)
    if not _owns_book(book, profile_id):
        frappe.throw(_("Only the publisher or the bookseller who added this book can edit it."), frappe.PermissionError)
    d = _json.loads(data) if isinstance(data, str) else (data or {})
    if "item_type" in d:
        # the owner can change what kind of item it is (a book, stationery, a gift, ...)
        allowed = (frappe.get_meta("Book").get_field("item_type").options or "Book").split("\n")
        if d["item_type"] not in allowed:
            frappe.throw(_("Unknown item type."))
        book.item_type = d["item_type"]
    is_book = (book.get("item_type") or "Book") == "Book"
    if "title" in d and not str(d["title"] or "").strip():
        frappe.throw(_("Title is required."))
    if is_book and "author" in d and not str(d["author"] or "").strip():
        frappe.throw(_("Author is required."))
    if is_book and "item_type" in d and not str(d.get("author") or book.author or "").strip():
        frappe.throw(_("Author is required for a book."))
    for key in ("title", "author", "title_en", "title_ur", "author_en", "language", "book_type", "binding", "isbn", "subtitle", "introduction", "description"):
        if key in d:
            book.set(key, (d[key] or "").strip() if isinstance(d[key], str) else d[key])
    if "page_count" in d and d["page_count"] not in (None, ""):
        book.page_count = int(d["page_count"])
    if "cover_image" in d:
        book.cover_image = d["cover_image"] or None
    if "set_mrp" in d and str(d["set_mrp"] or "").strip() != "":
        book.set_mrp = float(d["set_mrp"])
    for key in ("is_set", "is_bound"):
        if key in d:
            book.set(key, 1 if int(d[key] or 0) else 0)
    if "is_discontinued" in d:
        # no more production; the book stays in the publisher's list and a shop decides to show it or not
        book.is_discontinued = 1 if d["is_discontinued"] in (1, True, "1", "true") else 0
    for key in ("volume_count", "part_count"):
        if key in d:
            raw = str(d[key] if d[key] is not None else "").strip()
            book.set(key, int(raw) if raw.isdigit() else None)
    if "volumes" in d:
        # the volume rows of a multi-volume book (one row per volume; the set name is the book's name)
        rows = _json.loads(d["volumes"]) if isinstance(d["volumes"], str) else (d["volumes"] or [])
        book.set("volumes", [])
        for r in rows:
            book.append("volumes", {
                "volume_number": int(r.get("volume_number") or 0) or None,
                "title": (r.get("title") or "").strip() or None,
                "isbn": (r.get("isbn") or "").strip() or None,
                "volume_mrp": float(r.get("volume_mrp") or 0) or None,
                "loose_sale": 1 if r.get("loose_sale") else 0,
            })
    book.save(ignore_permissions=True)
    return {"name": book.name, "title": book.title, "author": book.author}


ITEM_UNITS = ("", "ml", "litre", "g", "kg", "piece", "dozen", "sheet", "ream", "inch", "cm")
ITEM_STYLES = ("", "Plain", "Fancy", "Premium")


@frappe.whitelist()
def set_item_options(profile_id, book_id, options=None, pack_lines=None):
    """The owner of a non-book item lists its choices: size, colour, style, amount (3 ml ... 100 ml), each with its
    own price and stock, and what a pack holds. The shop's stock of the item is the sum of the options' stock,
    and its listed price is the lowest option price ("from")."""
    import json as _json
    from frappe.utils import cint, flt
    from book_marketplace.auth.api.auth import _assert_profile_owner
    from book_marketplace.stock_management.api import stock as stock_api

    _assert_profile_owner(profile_id)
    book = frappe.get_doc("Book", book_id)
    if not _owns_book(book, profile_id):
        frappe.throw(_("Only the shop that added this item can change its options."), frappe.PermissionError)
    if (book.get("item_type") or "Book") == "Book":
        frappe.throw(_("Options are for items other than books. A book uses volumes and variants."))
    rows = _json.loads(options) if isinstance(options, str) else (options or [])
    lines = _json.loads(pack_lines) if isinstance(pack_lines, str) else (pack_lines or [])
    if len(rows) > 200:
        frappe.throw(_("Too many options (200 at most)."))

    clean, seen = [], set()
    for r in rows:
        size = " ".join(str(r.get("size") or "").split())[:60]
        colour = " ".join(str(r.get("colour") or "").split())[:60]
        style = str(r.get("style") or "").strip()
        unit = str(r.get("unit") or "").strip()
        amount = flt(r.get("amount"))
        price = flt(r.get("price"))
        stock = cint(r.get("stock_qty"))
        if style not in ITEM_STYLES:
            frappe.throw(_("Unknown style: {0}").format(style))
        if unit not in ITEM_UNITS:
            frappe.throw(_("Unknown unit: {0}").format(unit))
        if amount < 0 or price < 0 or stock < 0:
            frappe.throw(_("Amount, price and stock cannot be negative."))
        if not (size or colour or style or amount):
            continue  # an empty row
        if amount and not unit:
            frappe.throw(_("Choose the unit for {0} (ml, g, piece, ...).").format(amount))
        key = (size.lower(), colour.lower(), style, amount, unit)
        if key in seen:
            frappe.throw(_("The same option is written twice: {0}").format(" ".join(x for x in (size, colour, style, f"{amount:g} {unit}" if amount else "") if x)))
        seen.add(key)
        clean.append({"size": size or None, "colour": colour or None, "style": style or None,
                      "amount": amount or None, "unit": unit or None, "price": price or None, "stock_qty": stock})

    pack = []
    for ln in lines:
        name = " ".join(str(ln.get("pack_item") or "").split())[:120]
        if name:
            pack.append({"pack_item": name, "pack_qty": max(cint(ln.get("pack_qty")) or 1, 1)})

    book.set("item_options", clean)
    book.set("pack_lines", pack)
    prices = [r["price"] for r in clean if r["price"]]
    if prices:
        book.set_mrp = min(prices)
    book.save(ignore_permissions=True)

    if clean:
        stock_api.set_own_book_quantity(profile_id, book.name, sum(r["stock_qty"] for r in clean))
    if prices:
        item = frappe.db.get_value("BusinessCatalogItem", {"business_profile": profile_id, "book": book.name}, "name")
        if item:
            frappe.db.set_value("BusinessCatalogItem", item, "selling_price", min(prices))
    return {"name": book.name, "options": [o.as_dict() for o in book.item_options],
            "pack_lines": [x.as_dict() for x in book.pack_lines],
            "stock": sum(r["stock_qty"] for r in clean), "from_price": min(prices) if prices else None}


@frappe.whitelist()
def create_own_book(profile_id, title, author, book_type="Single Volume", language="Urdu", isbn=None,
                    set_mrp=None, trade_price=None, binding=None, page_count=None, weight=None, colours=None,
                    categories=None, is_bound=0, is_set=0, volume_count=None, part_count=None, languages=None):
    """Panel entry point for a publisher's new book. Goes through the matching rule (stage 1).
    Response shape kept for the panel: {name, title, status} on success, {error, existing} on refusal."""
    from book_marketplace.catalog.api.matching import add_publisher_book

    res = add_publisher_book(
        profile_id=profile_id, title=title, author=author, book_type=book_type or "Single Volume",
        language=language or "Urdu", isbn=isbn, set_mrp=set_mrp, trade_price=trade_price,
        binding=binding, page_count=page_count, weight=weight, colours=colours,
        categories=categories, is_bound=is_bound, is_set=is_set, volume_count=volume_count, part_count=part_count,
        languages=languages,
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
         "contact_phone", "contact_email", "address", "city", "province", "district", "map_link"],
        as_dict=True,
    )
    if not store:
        return None
    profile = frappe.db.get_value(
        "BusinessProfile", store.business_profile,
        ["business_name", "profile_type", "city", "logo", "banner_image"],
        as_dict=True,
    )
    store.business_name = profile.business_name if profile else ""
    store.profile_type = profile.profile_type if profile else ""
    # The business profile's logo and banner (uploaded in the business panel) show on the shop page.
    store.logo = (profile.logo if profile else "") or ""
    from book_marketplace.auth.api.shop_settings import shop_status
    store.open_status = shop_status(store.business_profile)
    store.banner_image = (profile.banner_image if profile else "") or store.banner_image or ""
    items = _store_items(store.business_profile, store.profile_type)
    catalog, cache = [], {}
    for item in items:
        book = frappe.db.get_value(
            "Book", item.book,
            [
                "title", "subtitle", "author", "book_type", "set_mrp", "language",
                "binding", "genre", "religious_segment", "introduction",
                "page_count", "weight", "colours", "dimensions",
                "publisher", "own_discount", "discount_percent", "is_discontinued",
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
        is_own = bool(book) and book.publisher == store.business_profile
        tracked = True
        if is_own:
            tracked = bool(frappe.db.exists(
                "StockLedger", {"book": item.book, "business_profile": store.business_profile}
            ))
        stock = None if not tracked else (stock or 0)
        # A shop shows its own copies. The publisher's status is only the publisher's own stock.
        pub_avail = frappe.db.get_value("Book", item.book, "availability") or "Available"
        # Price: a shop shows its own general price and its own discount;
        # a publisher's own book shows the book's MRP and the publisher's discount.
        if is_own:
            pv = price_view(item.selling_price or book.set_mrp, item.discount_percent or publisher_discount(book=book, _cache=cache))
        else:
            general = shop_general_price(store.business_profile, book.publisher if book else None,
                                         book.set_mrp if book else None, item.selling_price, item.book)
            pv = price_view(general, item.discount_percent)
        catalog.append({
            "advertised": bool(item.get("is_advertised")),
            "item_name": item.name,
            "book_id": item.book,
            "title": book.title if book else "",
            "subtitle": book.subtitle if book else "",
            "author": book.author if book else "",
            "publisher": book.publisher if book else "",
            "is_discontinued": frappe.utils.cint(book.is_discontinued) if book else 0,
            "owner_profile": book.owner_profile if book else "",
            "book_type": book.book_type if book else "",
            "mrp": book.set_mrp if book else None,
            "selling_price": pv["net_price"],
            "list_price": pv["list_price"],
            "discount_percent": pv["discount_percent"],
            "language": book.language if book else "",
            "binding": book.binding if book else "",
            "publisher_availability": pub_avail,
            "page_count": book.page_count if book else None,
            "weight": book.weight if book else None,
            "colours": book.colours if book else "",
            "genre": book.genre if book else "",
            "religious_segment": book.religious_segment if book else "",
            "introduction": book.introduction if book else "",
            "stock": stock,
            "categories": frappe.get_all("BookClassification", filters={"parent": item.book, "parenttype": "Book"}, pluck="category") if book else [],
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
        fields=["name", "book", "selling_price", "discount_percent", "is_advertised"],
        order_by="modified desc",
    ))
    # advertised books first; the order inside each group stays the same (newest changed first)
    items.sort(key=lambda i: not i.get("is_advertised"))
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
def create_or_update_store(profile_id, store_name, slug, description=None, contact_phone=None, contact_email=None, address=None, city=None, is_published=None, history=None, key_info=None, province=None, district=None, map_link=None):
    existing = frappe.db.get_value("BusinessStore", {"business_profile": profile_id}, "name")
    if existing:
        doc = frappe.get_doc("BusinessStore", existing)
    else:
        doc = frappe.get_doc({"doctype": "BusinessStore", "business_profile": profile_id})
    slug = (slug or "").strip()
    if not slug:
        frappe.throw(_("Handle is required."))
    if frappe.db.get_value("BusinessStore", {"slug": slug, "name": ["!=", doc.name or "__new__"]}, "name"):
        frappe.throw(_("This handle is already used by another store. Choose another."))
    doc.store_name = store_name
    doc.slug = slug
    doc.description = description
    doc.history = history
    doc.key_info = key_info
    doc.contact_phone = contact_phone
    doc.contact_email = contact_email
    doc.address = address
    doc.city = city
    doc.province = province or doc.get("province")
    doc.district = district or doc.get("district")
    doc.map_link = map_link if map_link is not None else doc.get("map_link")
    if is_published is not None:
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
def search_stores(text=None, city=None, profile_type=None, limit=60, offset=0):
    """Published stores, filtered by city (exact name), type and text. Publishers first, then by name."""
    filters = {"is_published": 1}
    if city:
        filters["city"] = city
    stores = frappe.get_all(
        "BusinessStore", filters=filters,
        fields=["name", "store_name", "slug", "description", "city", "province", "district", "business_profile"],
        limit_page_length=1000,
    )
    out = []
    needle = (text or "").strip().lower()
    for s in stores:
        profile = frappe.db.get_value("BusinessProfile", s.business_profile, ["business_name", "profile_type"], as_dict=True)
        s.business_name = profile.business_name if profile else ""
        s.profile_type = profile.profile_type if profile else ""
        s.store_url = f"/store/{s.slug}"
        s.province_name = frappe.db.get_value("Pakistan Location", s.province, "location_name") if s.province else ""
        if profile_type and s.profile_type != profile_type:
            continue
        if needle and needle not in (s.store_name or "").lower() and needle not in (s.business_name or "").lower():
            continue
        # Card details: the shop's logo, its book count and contact details (same count as the shop page).
        s.logo = frappe.db.get_value("BusinessProfile", s.business_profile, "logo") or ""
        s.catalog_count = len(_store_items(s.business_profile, s.profile_type))
        # a business that advertises its whole store (ad option Store) is shown first, with the label
        s.advertised = frappe.db.get_value("BusinessProfile", s.business_profile, "ad_option") == "Store"
        contact = frappe.db.get_value("BusinessStore", s.name, ["contact_phone", "contact_email", "address", "map_link"], as_dict=True) or {}
        s.contact_phone = contact.get("contact_phone") or ""
        s.contact_email = contact.get("contact_email") or ""
        s.address = contact.get("address") or ""
        out.append(s)
    out.sort(key=lambda x: (not x.advertised, x.profile_type != "Publisher", x.store_name or ""))
    start = int(offset or 0)
    return out[start:start + int(limit or 60)]


@frappe.whitelist(allow_guest=True)
def store_cities():
    """Cities that have at least one published store, with the store count."""
    rows = frappe.db.sql(
        "select city, count(*) c from `tabBusinessStore` where is_published=1 and ifnull(city,'')!='' group by city order by city",
        as_dict=True,
    )
    return [{"city": r.city, "stores": r.c} for r in rows]


@frappe.whitelist(allow_guest=True)
def get_filters():
    cats = frappe.get_all(
        "Book Category", filters={"category_type": "Subject", "section": ["is", "set"]},
        fields=["name", "category_name", "parent_book_category"], order_by="category_name asc",
        limit_page_length=0,
    )
    subjects = [{"name": c.name, "subject_name": c.category_name, "parent_subject": c.parent_book_category} for c in cats]
    languages = frappe.get_all("Book", fields=["language"], distinct=1)
    pubs = frappe.db.sql(
        """select p.name, p.business_name, count(*) as books from tabBook b join tabBusinessProfile p on p.name = b.publisher
           where b.global_visibility = 1 group by p.name, p.business_name order by books desc""", as_dict=True)
    return {
        "subjects": subjects,
        "languages": [l.language for l in languages if l.language],
        "publishers": [{"name": r.name, "label": r.business_name or r.name, "books": r.books} for r in pubs],
    }


# ─── Public offers: one book, many sellers ────────────────

def _offer_text_filter(text):
    return {"title": ["like", f"%{text}%"], "author": ["like", f"%{text}%"]}


@frappe.whitelist(allow_guest=True)
def list_authors(text=None, limit=400):
    """Authors of visible books, most books first, with the number of books. For the author filter."""
    limit = min(int(limit or 400), 1000)
    params = {"text": f"%{text.strip()}%"} if text and text.strip() else {}
    where = "global_visibility = 1 and ifnull(author, '') not in ('', '—', '-', '--')"
    if params:
        where += " and author like %(text)s"
    rows = frappe.db.sql(
        f"select author, count(*) c from tabBook where {where} group by author order by c desc, author asc limit %(limit)s",
        {**params, "limit": limit}, as_dict=True,
    )
    return [{"name": r.author, "books": r.c} for r in rows]


@frappe.whitelist(allow_guest=True)
def search_offers(text=None, subject=None, book_type=None, language=None, city=None, limit=24, offset=0, category=None, department=None, binding=None, form=None, author=None, with_commentaries=0, include_unlisted=0, publisher=None):
    """Each book once, with every published seller that stocks it.

    Sellers in `city` come first, and books that have a seller in `city`
    are listed before the rest. Books with no published seller are skipped.
    """
    limit = min(int(limit or 24), 100)
    offset = int(offset or 0)
    city = (city or "").strip()
    with_commentaries = str(with_commentaries) in ("1", "true", "True")
    include_unlisted = str(include_unlisted) in ("1", "true", "True")

    book_filters = {"global_visibility": 1}
    if author:
        book_filters["author"] = author
    if publisher:
        book_filters["publisher"] = publisher
    if book_type:
        book_filters["book_type"] = book_type
    if binding:
        book_filters["binding"] = binding
    if language:
        lang_books = set(frappe.get_all("BookLanguage", filters={"language": language, "parenttype": "Book"}, pluck="parent", limit_page_length=0))
        lang_books |= set(frappe.get_all("Book", filters={"language": language}, pluck="name", limit_page_length=0))
        if not lang_books:
            return {"books": [], "total": 0, "cities": _published_cities()}
        book_filters["name"] = ["in", list(lang_books)]
    or_filters = None
    if text:
        or_filters = _offer_text_filter(text)

    from book_marketplace.catalog.api.classification import book_names_for_categories, descendants

    for picked in (subject, category, department, form):
        if picked:
            picked_books = book_names_for_categories([picked])
            if with_commentaries and picked_books:
                # a commentary (شرح) shows with its text, so it takes the text's darja and department
                extra = set(frappe.get_all("Book", filters={"sharh_of": ["in", picked_books]}, pluck="name", limit_page_length=0))
                if extra and frappe.db.get_value("Book Category", picked, "category_type") == "Department":
                    # a commentary with a darja of its own elsewhere belongs to that darja, not to this one
                    inside = descendants([picked])
                    own_elsewhere = {r.parent for r in frappe.db.sql(
                        """select c.parent from tabBookClassification c join `tabBook Category` k on k.name = c.category
                           where c.parent in %(b)s and k.category_type = 'Department' and k.is_group = 0
                           and c.category not in %(inside)s""", {"b": tuple(extra), "inside": tuple(inside)}, as_dict=True)}
                    extra -= own_elsewhere
                picked_books = list(set(picked_books) | extra)
            if not picked_books:
                return {"books": [], "total": 0, "cities": _published_cities()}
            existing = book_filters.get("name")
            if existing:
                picked_books = [n for n in picked_books if n in existing[1]]
                if not picked_books:
                    return {"books": [], "total": 0, "cities": _published_cities()}
            book_filters["name"] = ["in", picked_books]

    books = frappe.get_all(
        "Book",
        filters=book_filters,
        or_filters=or_filters,
        fields=["name", "title", "subtitle", "author", "book_type", "language",
                "set_mrp", "cover_image", "primary_category", "genre", "religious_segment", "publisher",
                "own_discount", "discount_percent", "is_discontinued", "sharh_of"],
        order_by="modified desc",
        limit_page_length=2000,
    )
    if with_commentaries and books:
        # the text of each commentary found joins too (even with no shop), so the commentary shows under it
        have = [b.name for b in books]
        missing_texts = list({b.sharh_of for b in books if b.get("sharh_of")} - set(have))
        if missing_texts:
            books = list(books) + frappe.get_all(
                "Book", filters={"name": ["in", missing_texts], "global_visibility": 1},
                fields=["name", "title", "subtitle", "author", "book_type", "language",
                        "set_mrp", "cover_image", "primary_category", "genre", "religious_segment", "publisher",
                        "own_discount", "discount_percent", "is_discontinued", "sharh_of"])
        # commentaries of the books found join the results; the store shows them under their text
        have = [b.name for b in books]
        skip = set()
        if department:
            # a commentary with a darja of its own elsewhere belongs to that darja, not to this department view
            inside = tuple(descendants([department]))
            skip = {r.parent for r in frappe.db.sql(
                """select c.parent from tabBookClassification c join `tabBook Category` k on k.name = c.category
                   where k.category_type = 'Department' and k.is_group = 0 and c.category not in %(inside)s
                   and c.parent in (select name from tabBook where sharh_of in %(have)s)""",
                {"inside": inside, "have": tuple(have)}, as_dict=True)}
        books = list(books) + frappe.get_all(
            "Book",
            filters={"sharh_of": ["in", have], "name": ["not in", list(have) + list(skip)], "global_visibility": 1},
            fields=["name", "title", "subtitle", "author", "book_type", "language",
                    "set_mrp", "cover_image", "primary_category", "genre", "religious_segment", "publisher",
                    "own_discount", "discount_percent", "is_discontinued", "sharh_of"],
            limit_page_length=500,
        )
    if not books:
        return {"books": [], "total": 0, "cities": _published_cities()}

    book_names = [b.name for b in books]
    items = list(frappe.get_all(
        "BusinessCatalogItem",
        filters={"book": ["in", book_names], "is_active": 1},
        fields=["book", "business_profile", "selling_price", "discount_percent"],
    ))
    book_mrp = {b.name: b.set_mrp for b in books}
    book_by_name = {b.name: b for b in books}
    disc_cache = {}
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

    stock = {}
    if book_names:
        for r in frappe.db.sql(
            "select business_profile, book, sum(quantity) from `tabStockLedger` "
            "where state = 'Available' and book in %(books)s group by business_profile, book",
            {"books": book_names},
        ):
            stock[(r[0], r[1])] = int(r[2] or 0)

    offers_by_book = {}
    for item in items:
        store = store_by_profile.get(item.business_profile)
        if not store:
            continue
        profile = profile_by_name.get(item.business_profile)
        seller_city = store.city or (profile.city if profile else "") or ""
        book_row = book_by_name[item.book]
        general = shop_general_price(item.business_profile, book_row.publisher, book_mrp.get(item.book), item.selling_price, item.book)
        if item.business_profile == book_row.publisher:
            pv = price_view(item.selling_price or book_mrp.get(item.book),
                            item.discount_percent or publisher_discount(book=book_row, _cache=disc_cache))
        else:
            pv = price_view(general, item.discount_percent)
        offers_by_book.setdefault(item.book, []).append({
            "in_stock": stock.get((item.business_profile, item.book), 0) > 0,
            "business_profile": item.business_profile,
            "business_name": profile.business_name if profile else store.store_name,
            "store_name": store.store_name,
            "slug": store.slug,
            "profile_type": profile.profile_type if profile else "",
            "city": seller_city,
            "selling_price": pv["net_price"],
            "list_price": pv["list_price"],
            "discount_percent": pv["discount_percent"],
            "in_city": bool(city) and seller_city.strip().lower() == city.lower(),
        })

    # a text with no shop still shows when one of its commentaries is listed, so the group has a head
    texts_of_listed = {b.sharh_of for b in books if b.get("sharh_of") and offers_by_book.get(b.name)}
    result = []
    for book in books:
        sellers = offers_by_book.get(book.name)
        if not sellers:
            if not include_unlisted and not (with_commentaries and book.name in texts_of_listed):
                continue
            # syllabus view: the book is shown even if no shop lists it yet
            result.append({**book, "in_stock": False, "sellers": [], "seller_count": 0, "in_city_count": 0,
                           "min_price": None, "unlisted": True})
            continue
        sellers.sort(key=lambda s: (not s["in_city"], s["selling_price"] is None, s["selling_price"] or 0))
        prices = [s["selling_price"] for s in sellers if s["selling_price"] is not None]
        result.append({
            **book,
            "in_stock": any(x["in_stock"] for x in sellers),
            "sellers": sellers,
            "seller_count": len(sellers),
            "in_city_count": sum(1 for s in sellers if s["in_city"]),
            "min_price": min(prices) if prices else None,
        })

    if city:
        result.sort(key=lambda b: b["in_city_count"] == 0)
    if include_unlisted:
        result.sort(key=lambda b: bool(b.get("unlisted")))

    total = len(result)
    page = result[offset:offset + limit]
    _attach_classifications(page)
    _attach_publisher_names(page)
    return {"books": page, "total": total, "cities": _published_cities()}


def _attach_publisher_names(books):
    """The publisher's name on each card."""
    ids = list({b.get("publisher") for b in books if b.get("publisher")})
    names = {p.name: p.business_name for p in frappe.get_all(
        "BusinessProfile", filters={"name": ["in", ids]}, fields=["name", "business_name"])} if ids else {}
    for b in books:
        b["publisher_name"] = names.get(b.get("publisher"), "")


def _attach_classifications(books):
    """Each book's categories (department, book type, title type ...) for the cards. Only the books on this page."""
    if not books:
        return
    names = [b["name"] for b in books]
    rows = frappe.get_all(
        "BookClassification",
        filters={"parenttype": "Book", "parent": ["in", names]},
        fields=["parent", "category"],
        limit_page_length=0,
    )
    cat_names = list({r.category for r in rows if r.category})
    cats = {}
    if cat_names:
        for c in frappe.get_all(
            "Book Category",
            filters={"name": ["in", cat_names]},
            fields=["name", "category_name", "category_name_ur", "category_type"],
            limit_page_length=0,
        ):
            cats[c.name] = c
    by_book = {}
    for r in rows:
        c = cats.get(r.category)
        if c:
            by_book.setdefault(r.parent, []).append({
                "name": c.name,
                "label": c.category_name_ur or c.category_name,
                "label_en": c.category_name,
                "type": c.category_type,
            })
    for b in books:
        b["classifications"] = by_book.get(b["name"], [])


def _published_cities():
    rows = frappe.get_all("BusinessStore", filters={"is_published": 1}, fields=["city"], pluck="city")
    return sorted({c.strip() for c in rows if c and c.strip()})



@frappe.whitelist(allow_guest=True)
def pakistan_locations(parent=None, location_type=None):
    """Provinces (no parent), or the districts / cities under a parent. Urdu name first."""
    filters = {"parent_location": parent} if parent else {"parent_location": ["is", "not set"]}
    if location_type:
        filters["location_type"] = location_type
    return frappe.get_all("Pakistan Location", filters=filters,
                          fields=["name", "location_name", "location_name_en", "location_type"],
                          order_by="location_name_en asc", limit_page_length=0)
