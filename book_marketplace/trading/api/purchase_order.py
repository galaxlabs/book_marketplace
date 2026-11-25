"""Purchase orders. Phase 1: a shop orders books from a seller (publisher or shop) that lists them.
Phase 2: the seller writes the sent quantity per book and dispatches; the order then carries a QR.
The seller accepts or rejects. Phase 3: the buyer receives (received quantities, stock goes into its shop).
Phase 4: the agreed invoice is made from the received quantities; a short delivery is flagged for the seller."""
import json

import frappe
from frappe import _
from frappe.utils import flt

from book_marketplace.trading.api import policy


def _profile_of_user(profile_id):
    """The profile must belong to the logged-in account (admins may act for any profile)."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)


def _price_info(seller, book):
    """(mrp, discount_percent, net) for a book from a seller.
    MRP is the printed price (Book.set_mrp). A listed book uses the seller's trade or selling price.
    A publisher's own book not in its catalog uses the publisher's general discount rate on the MRP.
    Any other book must be listed by the seller."""
    mrp = flt(frappe.db.get_value("Book", book, "set_mrp"))
    row = frappe.db.get_value("BusinessCatalogItem", {"business_profile": seller, "book": book, "is_active": 1},
                              ["selling_price", "trade_price", "discount_percent"], as_dict=True)
    if row:
        # a listed book with no price of its own is sold at its MRP
        from book_marketplace.catalog.api.catalog import shop_general_price
        publisher = frappe.db.get_value("Book", book, "publisher")
        net = flt(row.trade_price) or shop_general_price(seller, publisher, mrp, row.selling_price, book) or mrp
        disc = flt(row.discount_percent) or (round((1 - net / mrp) * 100, 2) if mrp and net < mrp else 0)
        return mrp, disc, net
    if frappe.db.get_value("Book", book, "publisher") == seller:
        disc = flt(frappe.db.get_value("BusinessProfile", seller, "default_discount_percent"))
        return mrp, disc, round(mrp * (1 - disc / 100), 2)
    frappe.throw(_("This seller does not list the book {0}.").format(book))


def _listed_price(seller, book):
    return _price_info(seller, book)[2]


def _create_po(buyer_profile, seller_profile, items, notes="", by_principal=False, agent=None,
               agent_route=None, agent_pct=None):
    """Builds the order. by_principal: the publisher places it for a buyer (through an agent); never reachable by users."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    if by_principal:
        _assert_profile_owner(seller_profile)
    else:
        _profile_of_user(buyer_profile)
    policy.assert_can_order(buyer_profile)
    if buyer_profile == seller_profile:
        frappe.throw(_("Choose another business for the order."))
    if not frappe.db.exists("BusinessProfile", seller_profile):
        frappe.throw(_("Seller not found."))
    rows = json.loads(items) if isinstance(items, str) else (items or [])
    rows = [r for r in rows if r.get("book") and int(r.get("qty") or 0) > 0]
    if not rows:
        frappe.throw(_("Add at least one book with a quantity."))
    from book_marketplace.catalog.api import item_options as io
    from book_marketplace.trading.api import trade_policy as tp
    doc = frappe.get_doc({
        "doctype": "Purchase Order Request", "buyer_profile": buyer_profile, "seller_profile": seller_profile,
        "status": "Sent", "notes": (notes or "").strip()[:500],
    })
    # 1. the publisher's own price for each line (its list price, its own normal discount)
    lines = []
    for r in rows:
        mrp, disc, price = _price_info(seller_profile, r["book"])
        qty = int(r["qty"])
        line = {"book": r["book"], "qty": qty}
        ref = mrp
        sells_options = io.owner_of(r["book"]) == seller_profile and io.has_options(r["book"])
        if r.get("option"):
            if not sells_options:
                frappe.throw(_("This seller does not sell options of this item."))
            opt = io.get_option(r["book"], r["option"])
            if flt(opt.price):
                ref = flt(opt.price)
                price = round(flt(opt.price) * (1 - flt(disc) / 100), 2)
            line.update({"item_option": opt.name, "option_label": io.label_of(opt)})
        elif sells_options:
            frappe.throw(_("Choose the size / colour of {0}.").format(frappe.db.get_value("Book", r["book"], "title")))
        lines.append({"line": line, "qty": qty, "base": flt(price), "ref": flt(ref), "normal": flt(disc)})

    # 2. the publisher's trade policy for this shop and this bill (the bill is the total at the publisher's prices)
    bill = sum(l["base"] * l["qty"] for l in lines)
    rates = {}
    applied = None
    for l in lines:
        if l["normal"] not in rates:
            rates[l["normal"]] = tp.rate_for(seller_profile, buyer_profile, bill, l["normal"])
        res = rates[l["normal"]]
        if res["policy"]:
            applied = res
        rate, normal = res["rate"], l["normal"]
        # the policy can only lower a price, never raise it
        if l["ref"] > 0:
            policy_price = round(l["ref"] * (1 - rate / 100), 2)
            price = min(l["base"], policy_price) if rate > normal else l["base"]
        else:
            price = round(l["base"] * (1 - max(rate - normal, 0) / 100), 2)
        l["line"].update({"unit_price": price, "line_total": price * l["qty"]})
        doc.append("items", l["line"])
    doc.total_qty = sum(int(i.qty) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    if applied:
        doc.trade_policy = applied["policy_name"]
        doc.trade_rate = max(x["rate"] for x in rates.values())
    if agent:
        doc.agent = agent
        doc.agent_route = agent_route or "Customer account"
        doc.agent_commission_pct = flt(agent_pct)
    doc.insert(ignore_permissions=True)
    return _view(doc)


@frappe.whitelist()
def create_purchase_order(buyer_profile, seller_profile, items, notes=""):
    """items: JSON list of {book, qty}. The price is the seller's listed price (trade price if it has one)."""
    return _create_po(buyer_profile, seller_profile, items, notes)


def _key(book, option):
    """One order line: the item and its chosen option (the same item can be ordered in two sizes)."""
    return f"{book}|{option or ''}"


def _view(doc):
    return {
        "name": doc.name, "status": doc.status, "buyer_profile": doc.buyer_profile,
        "trade_policy": doc.get("trade_policy") or "", "trade_rate": flt(doc.get("trade_rate")),
        "agent": doc.get("agent") or "",
        "agent_name": (frappe.db.get_value("BusinessProfile", doc.agent, "business_name") or doc.agent) if doc.get("agent") else "",
        "agent_route": doc.get("agent_route") or "",
        "agent_commission_pct": flt(doc.get("agent_commission_pct")),
        "recovered": flt(frappe.db.sql("select coalesce(sum(base), 0) from `tabAgent Commission` where `order` = %s", doc.name)[0][0]),
        "buyer_name": frappe.db.get_value("BusinessProfile", doc.buyer_profile, "business_name"),
        "seller_profile": doc.seller_profile,
        "seller_name": frappe.db.get_value("BusinessProfile", doc.seller_profile, "business_name"),
        "notes": doc.notes or "", "total_qty": doc.total_qty, "total": flt(doc.total),
        "items": [{"book": i.book, "title": frappe.db.get_value("Book", i.book, "title"), "qty": i.qty,
                   "item_option": i.get("item_option") or "", "option_label": i.get("option_label") or "",
                   "sent_qty": i.sent_qty if i.sent_qty is not None else None,
                   "received_qty": i.received_qty if i.received_qty is not None else None,
                   "damaged_qty": int(i.damaged_qty or 0),
                   "unit_price": flt(i.unit_price), "line_total": flt(i.line_total)} for i in doc.items],
        "creation": str(doc.creation),
        "dispatched_on": str(doc.dispatched_on or ""),
        # the QR on the dispatched order holds only its number; the buyer scans it to open the invoice
        "qr": doc.name if doc.status in ("Dispatched", "Received", "Short") else "",
        "received_on": str(doc.received_on or ""),
        "receipt_submitted_on": str(doc.receipt_submitted_on or ""),
        "agreed_on": str(doc.agreed_on or ""),
        "receipt_note": doc.receipt_note or "",
        "damaged_qty": sum(int(i.damaged_qty or 0) for i in doc.items),
        "buyer_response": doc.buyer_response or "",
        "buyer_response_on": str(doc.buyer_response_on or ""),
        "buyer_response_image": doc.buyer_response_image or "",
        "freight_scenario": doc.freight_scenario or "", "freight_rule": doc.freight_rule or "",
        "freight_total": flt(doc.freight_total), "freight_buyer_share": flt(doc.freight_buyer_share),
        "freight_seller_share": flt(doc.freight_seller_share),
        "expires_on": str(_expiry(doc) or ""),
        "invoice_no": doc.invoice_no or "",
        "transporter": doc.transporter or "",
        "rated": bool(frappe.db.exists("Shop Rating", {"purchase_order": doc.name})),
        "bilty_no": doc.bilty_no or "",
        "rider_name": doc.rider_name or "",
        "rider_phone": doc.rider_phone or "",
        "invoice_total": flt(doc.invoice_total),
        "buyer_doc_no": doc.get("buyer_doc_no") or "", "seller_doc_no": doc.get("seller_doc_no") or "",
        "packing": {"same_city": bool(doc.get("same_city")), "cartons": int(doc.get("cartons") or 0),
                    "packets": int(doc.get("packets") or 0), "bundles": int(doc.get("bundles") or 0),
                    "tape_status": doc.get("tape_status") or "", "packing_charge": flt(doc.get("packing_charge")),
                    "labour_charge": flt(doc.get("labour_charge")), "station_cost": flt(doc.get("station_cost")),
                    "paid_by": doc.get("packing_paid_by") or ""},
        "freight_per_copy_buyer": round(flt(doc.freight_buyer_share) / int(doc.total_qty), 2) if int(doc.total_qty or 0) else 0,
        "freight_per_copy_seller": round(flt(doc.freight_seller_share) / int(doc.total_qty), 2) if int(doc.total_qty or 0) else 0,
        # a short delivery (received less than sent) is flagged so the seller sees it in its list
        "difference": doc.status == "Short",
        # shortage: books sent but not received; extra: books received above what was sent
        "difference_qty": sum(max(int(i.sent_qty or 0) - int(i.received_qty or 0), 0) for i in doc.items
                              if i.received_qty is not None),
        "extra_qty": sum(max(int(i.received_qty or 0) - int(i.sent_qty or 0), 0) for i in doc.items
                         if i.received_qty is not None),
    }


def _on_hand(profile_id, book):
    """The buyer's current stock of a book (all branches, Available)."""
    if not profile_id:
        return 0
    return sum(flt(q) for q in frappe.get_all("StockLedger", filters={"business_profile": profile_id, "book": book,
                                                                       "state": "Available"}, pluck="quantity"))


@frappe.whitelist()
def seller_catalog(seller_profile, buyer_profile=None, available_only=0):
    """Everything the seller can supply, for the buyer's order form: all books the seller publishes (its list),
    plus books it lists in its active catalog. Each row carries the buyer's on-hand quantity; books the buyer
    already holds come first, so the buyer can order more in the quantity box. Any logged-in account."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    rows = _seller_books(seller_profile)
    for r in rows:
        r["on_hand"] = _on_hand(buyer_profile, r["book"])
    if int(available_only or 0):
        rows = [r for r in rows if _on_hand(seller_profile, r["book"]) > 0]
    rows.sort(key=lambda x: (x["on_hand"] <= 0, (x["title"] or "").lower()))
    return rows


def _seller_books(seller_profile):
    """Each row: title, author, MRP, the discount rate that applies, the net price, and the estimate basis."""
    listed = {r.book for r in frappe.get_all(
        "BusinessCatalogItem", filters={"business_profile": seller_profile, "is_active": 1},
        fields=["book"], limit_page_length=0)}
    published = {b.name: b for b in frappe.get_all("Book", filters={"publisher": seller_profile},
                                                   fields=["name", "title", "author"], limit_page_length=0)}
    names = set(published) | listed
    out = []
    for name in names:
        b = published.get(name) or frappe.db.get_value("Book", name, ["name", "title", "author"], as_dict=True)
        if not b:
            continue
        mrp, disc, net = _price_info(seller_profile, name)
        row = {"book": name, "title": b.title, "author": b.author, "mrp": mrp,
               "discount_percent": disc, "price": net, "discount_amount": round(mrp - net, 2)}
        from book_marketplace.catalog.api import item_options as io
        if io.owner_of(name) == seller_profile and io.has_options(name):
            row["options"] = [{**o, "net_price": round((o["price"] or mrp) * (1 - flt(disc) / 100), 2)}
                              for o in io.options_of(name)]
        out.append(row)
    return sorted(out, key=lambda x: (x["title"] or "").lower())


@frappe.whitelist()
def find_sellers(text=""):
    """Business profiles of any type (publisher, bookseller, distributor, agent) whose name contains the text.
    The order form uses it to find the publisher or shop to order from. Any logged-in account."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    t = (text or "").strip()
    filters = {"business_name": ["like", f"%{t}%"]} if t else {}
    rows = frappe.get_all("BusinessProfile", filters=filters,
                          fields=["name", "business_name", "profile_type", "city"],
                          order_by="business_name asc", limit_page_length=1000)
    return [{"business_profile": r.name, "business_name": r.business_name, "profile_type": r.profile_type,
             "city": r.city or ""} for r in rows]


@frappe.whitelist()
def set_dispatch(name, profile_id, items, transporter=None, bilty_no=None, rider_name=None, rider_phone=None, freight_scenario=None,
                 cartons=None, packets=None, bundles=None, tape_status=None):
    """The seller prepares the order: writes the quantity it sends for each book, then packs and dispatches.
    items: JSON list of {book, sent_qty}. Only the seller, only while the order is Accepted."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller dispatches this order."), frappe.PermissionError)
    if doc.status != "Accepted":
        frappe.throw(_("Only an accepted order can be dispatched (now: {0}).").format(doc.status))
    sent = {_key(r.get("book"), r.get("item_option")): int(r.get("sent_qty") or 0)
            for r in (json.loads(items) if isinstance(items, str) else (items or []))}
    from book_marketplace.catalog.api import item_options as io
    for row in doc.items:
        k = _key(row.book, row.get("item_option"))
        if k not in sent:
            frappe.throw(_("Enter the sent quantity for {0}.").format(row.book))
        q = sent[k]
        if q < 0 or q > int(row.qty):
            frappe.throw(_("Sent quantity for {0} must be between 0 and {1}.").format(row.book, row.qty))
        row.sent_qty = q
        row.line_total = flt(row.unit_price) * q
        if row.get("item_option") and q:
            io.take(row.book, row.item_option, q)
            io.take_shop_stock(doc.seller_profile, row.book, q, "Purchase Order Request", doc.name)
    doc.total_qty = sum(int(i.sent_qty or 0) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    doc.transporter = (transporter or "").strip()[:140]
    doc.transport_company = frappe.db.get_value("Transport Company", {"name_of_company": doc.transporter}, "name") or None
    doc.bilty_no = (bilty_no or "").strip()[:60]
    # freight: quoted by the active freight rules for the scenario the seller picked
    from book_marketplace.trading.api.transport_policy import quote_freight
    scenario = (freight_scenario or "").strip()
    if scenario:
        city = frappe.db.get_value("BusinessProfile", doc.buyer_profile, "city") or ""
        q = quote_freight(scenario, city, doc.total_qty, doc.total)
        doc.freight_scenario = scenario
        doc.freight_rule = q["rule"] or ""
        doc.freight_total = q["total"]
        doc.freight_buyer_share = q["buyer_share"]
        doc.freight_seller_share = q["seller_share"]
    doc.rider_name = (rider_name or "").strip()[:140]
    doc.rider_phone = (rider_phone or "").strip()[:40]
    # packing: what was sent (cartons, packets, bundles) and its charges under the seller's packing rules
    from book_marketplace.trading.api import packing
    doc.same_city = 1 if packing.is_same_city(doc.seller_profile, doc.buyer_profile) else 0
    doc.cartons = max(int(cartons or 0), 0)
    doc.packets = max(int(packets or 0), 0)
    doc.bundles = max(int(bundles or 0), 0)
    doc.tape_status = (tape_status or "") if doc.cartons else ""
    c = packing.charges(packing.settings_of(doc.seller_profile), doc.cartons, doc.packets, doc.tape_status, doc.same_city)
    doc.packing_charge, doc.labour_charge, doc.station_cost = c["packing_charge"], c["labour_charge"], c["station_cost"]
    doc.packing_paid_by = c["packing_paid_by"]
    if not doc.get("seller_doc_no"):
        from book_marketplace.trading import doc_numbers
        doc.seller_doc_no = doc_numbers.next_number(doc.seller_profile, "BILL", doc.buyer_profile)
    doc.status = "Dispatched"
    doc.dispatched_on = now_datetime()
    doc.save(ignore_permissions=True)
    return _view(doc)


@frappe.whitelist()
def purchase_orders(profile_id, direction="buy"):
    """direction 'buy': orders this profile placed. 'sell': orders this profile received."""
    _profile_of_user(profile_id)
    field = "buyer_profile" if direction == "buy" else "seller_profile"
    names = frappe.get_all("Purchase Order Request", filters={field: profile_id}, pluck="name",
                           order_by="creation desc", limit_page_length=200)
    return [_view(frappe.get_doc("Purchase Order Request", n)) for n in names]


@frappe.whitelist()
def purchase_order_detail(name):
    doc = frappe.get_doc("Purchase Order Request", name)
    mine = [p for p in (doc.buyer_profile, doc.seller_profile)
            if frappe.db.get_value("BusinessProfile", p, "user") == frappe.session.user]
    if not mine and frappe.session.user != "Administrator":
        frappe.throw(_("This order is not yours."), frappe.PermissionError)
    return _view(doc)


@frappe.whitelist()
def respond_purchase_order(name, profile_id, decision):
    """The seller accepts or rejects a sent order. Only the seller can answer; the buyer can cancel while it is sent."""
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if decision not in ("Accepted", "Rejected", "Cancelled"):
        frappe.throw(_("Unknown decision."))
    if decision == "Cancelled":
        if profile_id != doc.buyer_profile:
            frappe.throw(_("Only the buyer can cancel."), frappe.PermissionError)
    elif profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller can accept or reject."), frappe.PermissionError)
    if doc.status != "Sent":
        frappe.throw(_("This order is already {0}.").format(doc.status))
    doc.status = decision
    doc.save(ignore_permissions=True)
    return _view(doc)


def _add_stock(profile_id, book, qty, ref=None):
    """Put received books into the buyer's own shop stock (its main branch)."""
    if qty <= 0:
        return
    from book_marketplace.stock_management.api.stock import _default_branch
    branch = _default_branch(profile_id)
    existing = frappe.db.get_value(
        "StockLedger",
        {"business_profile": profile_id, "book": book, "branch_location": branch, "state": "Available"},
        ["name", "quantity"], as_dict=True,
    )
    from book_marketplace.stock_management import movement
    ref_type, ref_name = ("Purchase Order Request", ref) if ref else (None, None)
    if existing:
        movement.set_qty(existing.name, flt(existing.quantity) + qty, "received", ref_type, ref_name)
    else:
        movement.add_row(book, profile_id, branch, qty, "Available", "received", ref_type, ref_name)


@frappe.whitelist()
def receive_purchase_order(name, profile_id, items):
    """The buyer records what arrived. items: JSON list of {book, received_qty, damaged_qty}.
    received_qty may be more or less than sent; damaged_qty are copies returned to the seller (not stocked, not billed).
    Nothing is stocked or billed yet: the seller must agree (agree_receipt). Status becomes Awaiting Agreement."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.buyer_profile:
        frappe.throw(_("Only the buyer records the receipt."), frappe.PermissionError)
    if doc.status not in ("Dispatched", "Awaiting Agreement"):
        frappe.throw(_("Only a dispatched order can be received (now: {0}).").format(doc.status))
    rows = json.loads(items) if isinstance(items, str) else (items or [])
    got = {_key(r.get("book"), r.get("item_option")): (int(r.get("received_qty") or 0), int(r.get("damaged_qty") or 0))
           for r in rows}
    for row in doc.items:
        k = _key(row.book, row.get("item_option"))
        if k not in got:
            frappe.throw(_("Enter the received quantity for {0}.").format(row.book))
        q, dmg = got[k]
        if q < 0:
            frappe.throw(_("Received quantity for {0} cannot be negative.").format(row.book))
        if dmg < 0 or dmg > q:
            frappe.throw(_("Damaged quantity for {0} must be between 0 and the received quantity.").format(row.book))
        row.received_qty = q
        row.damaged_qty = dmg
    doc.receipt_submitted_on = now_datetime()
    doc.status = "Awaiting Agreement"
    doc.save(ignore_permissions=True)
    return _view(doc)


@frappe.whitelist()
def agree_receipt(name, profile_id):
    """The seller agrees to the buyer's count. Only then: stock goes to the buyer, the invoice is final,
    and the khata shows the amount on both sides (seller: to receive; buyer: to pay)."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller agrees to the receipt."), frappe.PermissionError)
    if doc.status != "Awaiting Agreement":
        frappe.throw(_("Only a receipt waiting for agreement can be agreed (now: {0}).").format(doc.status))
    if doc.khata_posted:
        frappe.throw(_("This receipt is already posted."))
    short = False
    for row in doc.items:
        good = int(row.received_qty or 0) - int(row.damaged_qty or 0)
        if good < int(row.sent_qty or 0):
            short = True
        row.line_total = flt(row.unit_price) * good
    for row in doc.items:
        _add_stock(doc.buyer_profile, row.book, int(row.received_qty or 0) - int(row.damaged_qty or 0), doc.name)
    doc.total_qty = sum(int(i.received_qty or 0) - int(i.damaged_qty or 0) for i in doc.items)
    doc.total = sum(flt(i.line_total) for i in doc.items)
    doc.invoice_total = flt(doc.total)  # goods only; the freight is posted separately (khata and expenses)
    prefix = frappe.db.get_value("BusinessProfile", doc.seller_profile, "invoice_prefix") or "INV"
    doc.invoice_no = doc.get("seller_doc_no") or f"{prefix}-{doc.name}"
    doc.received_on = now_datetime()
    doc.agreed_on = now_datetime()
    doc.status = "Short" if short else "Received"
    if flt(doc.total) > 0:
        _post_khata(doc)
    doc.khata_posted = 1
    doc.save(ignore_permissions=True)
    policy.after_receipt(doc.buyer_profile)
    return _view(doc)


@frappe.whitelist()
def dispute_receipt(name, profile_id, note):
    """The seller does not agree: the order goes back to Dispatched; the buyer records the count again."""
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.seller_profile:
        frappe.throw(_("Only the seller answers the receipt."), frappe.PermissionError)
    if doc.status != "Awaiting Agreement":
        frappe.throw(_("Only a receipt waiting for agreement can be disputed (now: {0}).").format(doc.status))
    if not (note or "").strip():
        frappe.throw(_("Write why the seller does not agree."))
    doc.receipt_note = note.strip()
    doc.status = "Dispatched"
    doc.save(ignore_permissions=True)
    from book_marketplace.trading.api.notifications import notify
    notify(doc.buyer_profile, _("Receipt disputed: {0}").format(doc.name),
           _("The seller does not agree with your count. Write your explanation (and a photo if you have one)."),
           "/purchase-orders", kind="dispute")
    policy.after_receipt(doc.buyer_profile)
    return _view(doc)


def _post_khata(doc):
    """Both sides see the agreed amount: seller 'Credit Given' (party owes), buyer 'Credit Taken' (owes party)."""
    seller_name = frappe.db.get_value("BusinessProfile", doc.seller_profile, "business_name") or doc.seller_profile
    buyer_name = frappe.db.get_value("BusinessProfile", doc.buyer_profile, "business_name") or doc.buyer_profile
    amount = flt(doc.total)
    # on the agent route the agent is the one who owes the publisher (it pays if the buyer does not)
    payer = doc.buyer_profile
    payer_name = buyer_name
    if doc.get("agent") and doc.get("agent_route") == "Agent account":
        payer = doc.agent
        payer_name = frappe.db.get_value("BusinessProfile", payer, "business_name") or payer
    for profile, party, entry_type, other in (
        (doc.seller_profile, payer_name, "Credit Given", payer),
        (payer, seller_name, "Credit Taken", doc.seller_profile),
    ):
        frappe.get_doc({
            "doctype": "Khata Entry", "business_profile": profile, "party_name": party,
            "party_profile": other,
            "entry_type": entry_type, "amount": amount, "entry_date": frappe.utils.nowdate(),
            "reference": doc.invoice_no, "notes": f"Agreed receipt {doc.name}",
        }).insert(ignore_permissions=True)
    if doc.get("agent") and doc.get("agent_route") == "Agent account":
        from book_marketplace.trading.api.representatives import accrue_commission
        accrue_commission(doc, amount, "Agent account", doc.agent_commission_pct)
    _post_freight(doc)
    _post_packing(doc)


def _post_packing(doc):
    """Tape, packets and labour: billed to the buyer (khata both sides) or the seller's expense, by the seller's rule.
    The shop-to-station cost is always the seller's expense."""
    work = flt(doc.get("packing_charge")) + flt(doc.get("labour_charge"))
    seller_name = frappe.db.get_value("BusinessProfile", doc.seller_profile, "business_name") or doc.seller_profile
    buyer_name = frappe.db.get_value("BusinessProfile", doc.buyer_profile, "business_name") or doc.buyer_profile
    note = f"Packing on {doc.name}: {int(doc.get('cartons') or 0)} cartons, {int(doc.get('packets') or 0)} packets"
    if work > 0 and doc.get("packing_paid_by") == "Buyer":
        for profile, party, entry_type in ((doc.seller_profile, buyer_name, "Credit Given"),
                                           (doc.buyer_profile, seller_name, "Credit Taken")):
            frappe.get_doc({"doctype": "Khata Entry", "business_profile": profile, "party_name": party,
                            "entry_type": entry_type, "amount": work, "entry_date": frappe.utils.nowdate(),
                            "reference": doc.name, "notes": note}).insert(ignore_permissions=True)
    seller_cost = flt(doc.get("station_cost")) + (work if doc.get("packing_paid_by") == "Seller" else 0)
    if seller_cost > 0:
        frappe.get_doc({"doctype": "Shop Expense", "business_profile": doc.seller_profile, "category": "Packing",
                        "amount": seller_cost, "expense_date": frappe.utils.getdate(frappe.utils.nowdate()),
                        "note": (note + " (incl. shop to station)")[:300]}).insert(ignore_permissions=True)


def _post_freight(doc):
    """The payer of the freight owes the transport company (khata) and records a Transport expense.
    Each payer's per-copy freight is shown on the order, so its cost can be read per book."""
    company = (doc.transporter or "").strip() or "Transport"
    for payer, share in ((doc.buyer_profile, flt(doc.freight_buyer_share)),
                         (doc.seller_profile, flt(doc.freight_seller_share))):
        if share <= 0:
            continue
        note = f"Freight on {doc.name} ({doc.freight_scenario or ''})"
        frappe.get_doc({
            "doctype": "Khata Entry", "business_profile": payer, "party_name": company,
            "entry_type": "Credit Taken", "amount": share, "entry_date": frappe.utils.nowdate(),
            "reference": doc.name, "notes": note,
        }).insert(ignore_permissions=True)
        frappe.get_doc({
            "doctype": "Shop Expense", "business_profile": payer, "category": "Transport",
            "amount": share, "expense_date": frappe.utils.getdate(frappe.utils.nowdate()),
            "note": note[:300],
        }).insert(ignore_permissions=True)


@frappe.whitelist()
def return_summary(profile_id):
    """Seller view: damaged copies to return, per buyer city and book, from receipts on the seller's side."""
    _profile_of_user(profile_id)
    rows = frappe.db.sql("""
        select b.city as city, i.book as book, sum(i.damaged_qty) as damaged
        from `tabPurchase Order Request Item` i
        inner join `tabPurchase Order Request` p on p.name = i.parent
        inner join `tabBusinessProfile` b on b.name = p.buyer_profile
        where p.seller_profile = %s and p.status in ('Awaiting Agreement', 'Received', 'Short')
          and ifnull(i.damaged_qty, 0) > 0
        group by b.city, i.book
        order by b.city, i.book
    """, (profile_id,), as_dict=True)
    for r in rows:
        r["title"] = frappe.db.get_value("Book", r["book"], "title")
    return rows


def _expiry(doc):
    """An order waiting for the seller's agreement ends 14 days after the receipt was recorded."""
    if doc.status == "Awaiting Agreement" and doc.receipt_submitted_on:
        return frappe.utils.add_days(doc.receipt_submitted_on, 14)
    return None


@frappe.whitelist()
def respond_to_dispute(name, profile_id, response, image=None):
    """The shop explains its position after the seller disputed the receipt. The seller sees it with the order."""
    from frappe.utils import now_datetime
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Purchase Order Request", name)
    if profile_id != doc.buyer_profile:
        frappe.throw(_("Only the buyer answers the dispute."), frappe.PermissionError)
    if doc.status not in ("Dispatched", "Awaiting Agreement"):
        frappe.throw(_("A response can be written only while the order is open (now: {0}).").format(doc.status))
    if not doc.receipt_note:
        frappe.throw(_("A response is needed only after the seller disputes the receipt."))
    text = (response or "").strip()
    if not text:
        frappe.throw(_("Write your explanation."))
    if image and not (str(image).startswith("/files/") or str(image).startswith("https://")):
        frappe.throw(_("The photo must be an uploaded file."))
    doc.buyer_response = text
    doc.buyer_response_image = (image or None)
    doc.buyer_response_on = now_datetime()
    doc.save(ignore_permissions=True)
    return _view(doc)


def expire_stale_receipts():
    """Daily job: a receipt not agreed within 14 days expires. No stock, no invoice, no khata entry."""
    from frappe.utils import add_days, now_datetime
    cutoff = add_days(now_datetime(), -14)
    names = frappe.get_all(
        "Purchase Order Request",
        filters={"status": "Awaiting Agreement", "receipt_submitted_on": ["<=", cutoff]},
        pluck="name",
    )
    buyers = set()
    from book_marketplace.trading.api.notifications import notify
    for n in names:
        doc = frappe.get_doc("Purchase Order Request", n)
        doc.status = "Expired"
        doc.save(ignore_permissions=True)
        buyers.add(doc.buyer_profile)
        for side in (doc.buyer_profile, doc.seller_profile):
            notify(side, _("Order expired: {0}").format(doc.name),
                   _("The receipt was not agreed within 14 days. No stock or khata change was made."),
                   "/purchase-orders", kind="expiry")
    for b in buyers:
        policy.after_receipt(b)
    return len(names)
