import json

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, now_datetime

ONLINE_HOURS = 2     # owner's decision: online payment window
MANUAL_HOURS = 24    # owner's decision: shop confirmation window (manual / pay-at-shop)
REVIVE_DAYS = 7      # owner's decision: an expired order can be revived for 7 days
WAIT_STATUS = {"online": "Awaiting Payment", "manual": "Awaiting Shop Confirmation"}
HOURS = {"online": ONLINE_HOURS, "manual": MANUAL_HOURS}


def _buyer_from_session():
    name = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if not name:
        frappe.throw(_("Please log in as a buyer to order."), frappe.PermissionError)
    return name


def _store_open(seller):
    """A seller takes orders only when it is approved and its store is published."""
    status = frappe.db.get_value("BusinessProfile", seller, "status")
    return status == "Approved" and bool(frappe.db.exists("BusinessStore", {"business_profile": seller, "is_published": 1}))


def _offers(book):
    from book_marketplace.catalog.api.catalog import price_view, publisher_discount

    bk = frappe.db.get_value("Book", book, ["set_mrp", "publisher", "global_visibility"], as_dict=True)
    if not bk or not bk.global_visibility:
        return []
    cands = {}
    for r in frappe.get_all("Business Catalog Item", filters={"book": book, "is_active": 1},
                            fields=["business_profile", "selling_price", "discount_percent"]):
        disc = flt(r.discount_percent)
        if not disc and r.business_profile == bk.publisher:
            disc = publisher_discount(book_id=book)
        from book_marketplace.catalog.api.catalog import shop_general_price
        cands[r.business_profile] = (shop_general_price(r.business_profile, bk.publisher, bk.set_mrp, r.selling_price, book), disc)
    out = []
    for seller, (list_price, pct) in cands.items():
        if not _store_open(seller):
            continue
        led = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Available"},
                                  ["name", "quantity", "branch_location"], as_dict=True)
        have = int(led.quantity or 0) if led else 0
        out.append({
            "seller": seller,
            "business_name": frappe.db.get_value("BusinessProfile", seller, "business_name"),
            "unit_price": price_view(list_price, pct)["net_price"] or 0,
            "have": have,
            "ledger": led.name if led else None,
            "branch": led.branch_location if led else None,
        })
    return out


def _reserve(seller, book, qty, ledger, branch, ref=None):
    from book_marketplace.stock_management import movement
    ref_type, ref_name = ("Customer Order", ref) if ref else (None, None)
    cur = frappe.db.sql("select quantity from `tabStockLedger` where name=%s for update", ledger)
    have = int(cur[0][0] or 0) if cur else 0
    if have < qty:
        return False
    movement.set_qty(ledger, have - qty, "reserved", ref_type, ref_name)
    res = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Reserved",
                                              "branch_location": branch}, ["name", "quantity"], as_dict=True)
    if res:
        movement.set_qty(res.name, int(res.quantity or 0) + qty, "reserved", ref_type, ref_name)
    else:
        movement.add_row(book, seller, branch, qty, "Reserved", "reserved", ref_type, ref_name)
    return True


def _release(seller, book, qty, option=None, ref=None):
    from book_marketplace.trading.api.customer_portal import notify_back_in_stock
    from book_marketplace.stock_management import movement
    ref_type, ref_name = ("Customer Order", ref) if ref else (None, None)
    if option:
        from book_marketplace.catalog.api import item_options as io
        io.give_back(book, option, qty)
    before = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Available"}, "quantity") or 0
    res = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Reserved"},
                              ["name", "quantity", "branch_location"], as_dict=True)
    if res:
        movement.set_qty(res.name, int(res.quantity or 0) - qty, "released", ref_type, ref_name)
    av = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Available"}, "name")
    if av:
        movement.change_qty(av, qty, "released", ref_type, ref_name)
    elif res:
        movement.add_row(book, seller, res.branch_location, qty, "Available", "released", ref_type, ref_name)
    notify_back_in_stock(seller, book, before, int(before) + qty)


def basket_view(name, buyer):
    b = frappe.db.get_value("Customer Basket", name, ["name", "buyer_profile", "grand_total"], as_dict=True)
    if not b or b.buyer_profile != buyer:
        frappe.throw(_("Not found."), frappe.PermissionError)
    orders = frappe.get_all("Customer Order", filters={"basket": name},
                            fields=["name", "seller_profile", "status", "total", "reserved_until", "payment_mode"])
    for o in orders:
        o["business_name"] = frappe.db.get_value("BusinessProfile", o.seller_profile, "business_name")
        o.pop("seller_profile", None)
    return {"basket": b.name, "grand_total": b.grand_total, "orders": orders}


DELIVERY_MODES = {"pickup": "Pickup at shop", "local": "Local delivery", "courier": "Courier"}


def _delivery(delivery):
    """Normalises the buyer's delivery choice. Pickup needs nothing; delivery needs a full address."""
    if isinstance(delivery, str):
        delivery = json.loads(delivery) if delivery.strip() else {}
    delivery = delivery or {}
    mode = DELIVERY_MODES.get(delivery.get("mode") or "pickup")
    if not mode:
        frappe.throw(_("Unknown delivery choice."))
    out = {"delivery_mode": mode, "courier_service": "", "ship_name": "", "ship_phone": "",
           "ship_city": "", "ship_address": "", "ship_postal": ""}
    if mode == "Pickup at shop":
        return out
    for key, label in (("name", "name"), ("phone", "phone"), ("city", "city"), ("address", "address")):
        val = (delivery.get(key) or "").strip()
        if not val:
            frappe.throw(_("Please write your {0} for delivery.").format(_(label)))
        out["ship_" + ("name" if key == "name" else key)] = val
    out["ship_postal"] = (delivery.get("postal") or "").strip()
    if mode == "Courier":
        out["courier_service"] = (delivery.get("service") or "").strip()[:140]
    else:
        out["courier_service"] = "Nearest shop / قریب ترین دکان"
    return out


def delivery_view(doc):
    return {"mode": doc.delivery_mode or "Pickup at shop", "service": doc.courier_service or "",
            "name": doc.ship_name or "", "phone": doc.ship_phone or "", "city": doc.ship_city or "",
            "address": doc.ship_address or "", "postal": doc.ship_postal or ""}


def checkout_for(buyer, client_id, items, choices=None, payment_mode="online", delivery=None):
    """Checkout core. payment_mode: 'online' (proof, 2 h) or 'manual' (pay at shop / by hand, shop confirms, 24 h)."""
    if payment_mode not in WAIT_STATUS:
        frappe.throw(_("Unknown payment choice."))
    ship = _delivery(delivery)
    client_id = (client_id or "").strip()
    if client_id:
        existing = frappe.db.get_value("Customer Basket", {"client_id": client_id}, ["name", "buyer_profile"], as_dict=True)
        if existing:
            if existing.buyer_profile != buyer:
                frappe.throw(_("This basket belongs to another buyer."), frappe.PermissionError)
            return basket_view(existing.name, buyer)
    if isinstance(items, str):
        items = json.loads(items)
    if isinstance(choices, str):
        choices = json.loads(choices) if choices.strip() else {}
    choices = choices or {}
    from book_marketplace.catalog.api import item_options as io
    merged = {}
    for it in items or []:
        book, qty, option = it.get("book"), int(it.get("qty") or 0), (it.get("option") or "")
        if not book or qty < 1:
            frappe.throw(_("Each item needs a book and a quantity of at least 1."))
        if not option and io.has_options(book):
            return {"ok": False, "needs_option": True, "book": book, "options": io.options_of(book)}
        merged[(book, option)] = merged.get((book, option), 0) + qty
    if not merged:
        frappe.throw(_("The basket is empty."))

    plan, problems = {}, []
    for (book, option), qty in merged.items():
        offers = _offers(book)
        opt = None
        if option:
            # a chosen size / colour is sold by the shop that owns the item, from that option's own stock
            opt = io.get_option(book, option)
            owner = io.owner_of(book)
            offers = [dict(o, have=min(o["have"], int(opt.stock_qty or 0))) for o in offers if o["seller"] == owner]
            if flt(opt.price):
                from book_marketplace.catalog.api.catalog import price_view
                for o in offers:
                    pct = frappe.db.get_value("Business Catalog Item", {"business_profile": o["seller"], "book": book}, "discount_percent")
                    o["unit_price"] = price_view(flt(opt.price), flt(pct))["net_price"] or flt(opt.price)
        ok = [o for o in offers if o["have"] >= qty]
        if not ok:
            problems.append({"book": book, "reason": "not_enough_stock",
                             "available": max([o["have"] for o in offers] or [0])})
            continue
        if len(ok) > 1 and not choices.get(book):
            return {"ok": False, "needs_choice": True, "book": book,
                    "choices": [{"seller": o["seller"], "business_name": o["business_name"],
                                 "unit_price": o["unit_price"], "have": o["have"]} for o in ok]}
        pick = ok[0] if len(ok) == 1 else next((o for o in ok if o["seller"] == choices.get(book)), None)
        if not pick:
            problems.append({"book": book, "reason": "choice_not_available"})
            continue
        plan.setdefault(pick["seller"], []).append({"book": book, "qty": qty, "unit_price": pick["unit_price"],
                                                    "ledger": pick["ledger"], "branch": pick["branch"],
                                                    "option": option, "label": io.label_of(opt) if opt else ""})
    if problems:
        return {"ok": False, "problems": problems}

    basket = frappe.get_doc({"doctype": "Customer Basket", "buyer_profile": buyer,
                             "client_id": client_id or None, "created_on": now_datetime()})
    basket.insert(ignore_permissions=True)
    deadline = add_to_date(now_datetime(), hours=HOURS[payment_mode])
    grand, orders = 0, []
    for seller, lines in plan.items():
        for ln in lines:
            if not _reserve(seller, ln["book"], ln["qty"], ln["ledger"], ln["branch"]):
                frappe.db.rollback()
                return {"ok": False, "problems": [{"book": ln["book"], "reason": "not_enough_stock"}]}
            if ln["option"]:
                io.take(ln["book"], ln["option"], ln["qty"])
        total = sum(ln["qty"] * ln["unit_price"] for ln in lines)
        grand += total
        order = frappe.get_doc({
            "doctype": "Customer Order", "basket": basket.name, "buyer_profile": buyer, "seller_profile": seller,
            "status": WAIT_STATUS[payment_mode], "payment_mode": payment_mode, "payment_status": "Unpaid",
            "reserved_until": deadline, "subtotal": total, "total": total, **ship,
            "items": [{"book": ln["book"], "qty": ln["qty"], "unit_price": ln["unit_price"],
                       "line_total": ln["qty"] * ln["unit_price"],
                       "item_option": ln["option"] or None, "option_label": ln["label"] or None} for ln in lines],
        })
        order.insert(ignore_permissions=True)
        orders.append({"name": order.name, "seller": seller, "total": total})
    frappe.db.set_value("Customer Basket", basket.name, "grand_total", grand)
    return {"ok": True, "basket": basket.name, "grand_total": grand, "reserved_until": str(deadline), "orders": orders}


@frappe.whitelist(allow_guest=True)
def book_offers(book):
    """The shops that have this book in stock, each with its own price (lowest first). The book page shows
    these prices; checkout uses the same rule, so what the page shows is what the cart charges."""
    if not book or not frappe.db.exists("Book", book):
        return []
    rows = [o for o in _offers(book) if o["have"] > 0]
    rows.sort(key=lambda o: (flt(o["unit_price"]), o["business_name"] or ""))
    return [{"seller": o["seller"], "business_name": o["business_name"], "unit_price": flt(o["unit_price"]),
             "have": int(o["have"])} for o in rows]


@frappe.whitelist()
def checkout(client_id=None, items=None, choices=None, payment_mode="online", delivery=None):
    return checkout_for(_buyer_from_session(), client_id, items, choices, payment_mode, delivery)


@frappe.whitelist()
def my_basket(name):
    return basket_view(name, _buyer_from_session())


def expire_overdue():
    """Every 10 minutes: orders past their window (2 h online, 24 h manual) become Expired; copies go back to stock.
    Expired orders stay, and can be revived for 7 days."""
    due = frappe.get_all("Customer Order",
                         filters={"status": ["in", list(WAIT_STATUS.values())], "reserved_until": ["<", now_datetime()]},
                         fields=["name", "seller_profile"])
    for o in due:
        items = frappe.get_all("Customer Order Item", filters={"parent": o.name, "parenttype": "Customer Order"},
                               fields=["book", "qty", "item_option"])
        for it in items:
            _release(o.seller_profile, it.book, int(it.qty), it.item_option)
        frappe.db.set_value("Customer Order", o.name, {"status": "Expired", "expired_on": now_datetime()})
    return len(due)


def _may_act_on(order_doc):
    """The buyer of the order, or the shop that owns it."""
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if buyer and buyer == order_doc.buyer_profile:
        return True
    shops = frappe.get_all("BusinessProfile", filters={"user": frappe.session.user}, pluck="name")
    if order_doc.seller_profile in shops or frappe.session.user == "Administrator":
        return True
    from book_marketplace.auth.permissions import has_access
    return has_access(order_doc.seller_profile, "orders.manage")


@frappe.whitelist()
def revive_order(order):
    """Bring an expired order back under the same number. Stock must be available now; the window restarts."""
    doc = frappe.get_doc("Customer Order", order)
    if not _may_act_on(doc):
        frappe.throw(_("Not your order."), frappe.PermissionError)
    if doc.status != "Expired":
        frappe.throw(_("Only an expired order can be revived."))
    if doc.expired_on and frappe.utils.add_days(doc.expired_on, REVIVE_DAYS) < now_datetime():
        frappe.throw(_("This order can no longer be revived (more than {0} days).").format(REVIVE_DAYS))
    mode = doc.payment_mode or "online"
    short = []
    ledgers = []
    for it in doc.items:
        led = frappe.db.get_value("StockLedger", {"business_profile": doc.seller_profile, "book": it.book, "state": "Available"},
                                  ["name", "quantity", "branch_location"], as_dict=True)
        have = int(led.quantity or 0) if led else 0
        if have < int(it.qty):
            short.append({"book": it.book, "wanted": int(it.qty), "available": have})
        ledgers.append((it, led))
    if short:
        frappe.throw(_("Not enough copies to revive this order: {0}").format(
            ", ".join(f"{frappe.db.get_value('Book', s['book'], 'title')} ({s['available']})" for s in short)))
    from book_marketplace.catalog.api import item_options as io
    for it, led in ledgers:
        if not _reserve(doc.seller_profile, it.book, int(it.qty), led.name, led.branch_location):
            frappe.throw(_("Stock changed; please try again."))
        if it.get("item_option"):
            io.take(it.book, it.item_option, int(it.qty))
    doc.status = WAIT_STATUS[mode]
    doc.reserved_until = add_to_date(now_datetime(), hours=HOURS[mode])
    doc.expired_on = None
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status, "reserved_until": str(doc.reserved_until)}
# An order can be deleted only before the shop accepts it: work has not started on it yet.
DELETABLE = {"Awaiting Payment", "Awaiting Shop Confirmation", "Payment Under Review", "Cancelled", "Rejected", "Expired"}
# Orders still holding reserved copies: the copies go back to stock on delete, as on cancel.
HOLDS_STOCK = {"Awaiting Payment", "Awaiting Shop Confirmation", "Payment Under Review"}


@frappe.whitelist()
def delete_cancelled_order(order):
    """Delete an order (buyer or shop). Allowed only while the shop has not accepted it. A log is kept."""
    doc = frappe.get_doc("Customer Order", order)
    if not _may_act_on(doc):
        frappe.throw(_("Not your order."), frappe.PermissionError)
    if doc.status not in DELETABLE:
        frappe.throw(_("This order is already accepted or in progress, so it cannot be deleted."))
    _write_deletion_log(doc)
    if doc.status in HOLDS_STOCK:
        for it in doc.items:
            _release(doc.seller_profile, it.book, int(it.qty), it.get("item_option"))
    frappe.delete_doc("Customer Order", order, ignore_permissions=True, force=True)
    return {"name": order, "deleted": True}


def _write_deletion_log(doc):
    buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if frappe.session.user == "Administrator":
        side = "Administrator"
    elif buyer and buyer == doc.buyer_profile:
        side = "Buyer"
    else:
        side = "Shop"
    items = [{"book": i.book, "qty": i.qty, "unit_price": i.unit_price, "line_total": i.line_total,
              "item_option": i.get("item_option") or ""} for i in doc.items]
    frappe.get_doc({
        "doctype": "Deleted Order Log",
        "order_no": doc.name,
        "order_status": doc.status,
        "deleted_on": now_datetime(),
        "deleted_by": frappe.session.user,
        "deleted_side": side,
        "seller_profile": doc.seller_profile,
        "buyer_profile": doc.buyer_profile,
        "total": doc.total,
        "payment_mode": doc.payment_mode or "",
        "items_json": json.dumps(items, ensure_ascii=False, default=str),
    }).insert(ignore_permissions=True)
