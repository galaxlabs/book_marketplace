"""Customer orders, step 3: payment proof, seller decision, status changes (table in WORKER_INSTRUCTIONS section 3.2).
Goes to book_marketplace/trading/api/customer_payment.py on branch feat/customer-orders (dev-b)."""
import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from book_marketplace.trading.api.customer_order import _release, _buyer_from_session


class ConflictError(frappe.ValidationError):
    """Wrong status change: the API answers 409 (owner's rule)."""
    http_status_code = 409


# (from, to) -> who may do it: "buyer", "seller"
TRANSITIONS = {
    ("Awaiting Payment", "Payment Under Review"): "buyer",
    ("Payment Under Review", "Accepted"): "seller",
    ("Payment Under Review", "Awaiting Payment"): "seller",
    ("Awaiting Payment", "Rejected"): "seller",
    ("Payment Under Review", "Rejected"): "seller",
    ("Awaiting Payment", "Cancelled"): "buyer",
    ("Payment Under Review", "Cancelled"): "buyer",
    ("Awaiting Payment", "Expired"): "system",
    ("Accepted", "Packed"): "seller",
    ("Packed", "Dispatched"): "seller",
    ("Dispatched", "Delivered"): "seller",
}
RELEASING = {"Rejected", "Cancelled", "Expired"}  # copies go back to stock


def _order_owner_check(order, actor):
    if actor == "buyer":
        buyer = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
        if not buyer or buyer != frappe.db.get_value("Customer Order", order, "buyer_profile"):
            frappe.throw(_("Not your order."), frappe.PermissionError)
    elif actor == "seller":
        from book_marketplace.auth.api.auth import _assert_profile_owner
        _assert_profile_owner(frappe.db.get_value("Customer Order", order, "seller_profile"))


def _move(order, to, actor, reason=None):
    """The one place where an order's status changes. Wrong moves raise ConflictError (409)."""
    doc = frappe.get_doc("Customer Order", order)
    frm = doc.status
    who = TRANSITIONS.get((frm, to))
    if not who or (who != "system" and who != actor):
        raise ConflictError(_("Cannot change from {0} to {1}.").format(frm, to))
    _order_owner_check(order, who if who != "system" else "seller")
    doc.status = to
    stamp = {"Accepted": "accepted_on", "Packed": "packed_on", "Dispatched": "dispatched_on", "Delivered": "delivered_on"}.get(to)
    if stamp:
        setattr(doc, stamp, now_datetime())
    if to in ("Rejected", "Cancelled") and reason:
        doc.rejection_reason = reason
    doc.save(ignore_permissions=True)
    if to in RELEASING:
        for it in doc.items:
            _release(doc.seller_profile, it.book, int(it.qty), it.get("item_option"))
    if to == "Delivered":
        for it in doc.items:
            _consume_reserved(doc.seller_profile, it.book, int(it.qty))
    _emit(order, frm, to, doc.seller_profile, doc.buyer_profile)
    return {"name": order, "status": to}


def _consume_reserved(seller, book, qty):
    res = frappe.db.get_value("StockLedger", {"business_profile": seller, "book": book, "state": "Reserved"}, ["name", "quantity"], as_dict=True)
    if res:
        frappe.db.set_value("StockLedger", res.name, "quantity", max(0, int(res.quantity or 0) - qty))


def _emit(order, frm, to, seller, buyer):
    """Hook for later notifications. Nothing is sent now (owner's rule)."""
    for fn in frappe.get_hooks("on_order_status_changed") or []:
        frappe.get_attr(fn)({"order": order, "from": frm, "to": to, "seller": seller, "buyer": buyer})


@frappe.whitelist()
def submit_payment_proof(order, method, reference_no, amount=None, screenshot=None):
    """Buyer pays by hand (bank, Easypaisa, JazzCash) and sends the reference. The order moves to Payment Under Review."""
    buyer = _buyer_from_session()
    doc = frappe.get_doc("Customer Order", order)
    if doc.buyer_profile != buyer:
        frappe.throw(_("Not your order."), frappe.PermissionError)
    if not (reference_no or "").strip():
        frappe.throw(_("Please write the payment reference."))
    amt = flt(amount) or flt(doc.total)
    if amt < flt(doc.total):
        frappe.throw(_("The amount is less than the order total ({0}).").format(doc.total))
    pay = frappe.get_doc({
        "doctype": "PaymentTransaction",
        "customer_order": order,
        "buyer_profile": buyer,
        "seller_profile": doc.seller_profile,
        "amount": amt,
        "payment_method": method,
        "reference_no": reference_no.strip(),
        "payment_date": frappe.utils.nowdate(),
        "screenshot": screenshot,
        "status": "Pending",
    }).insert(ignore_permissions=True)
    _move(order, "Payment Under Review", "buyer")
    return {"payment": pay.name, "order": order, "status": "Payment Under Review"}


@frappe.whitelist()
def seller_decide_payment(order, decision, reason=None):
    """Seller checks the money: verify (order Accepted), reject the proof (buyer must pay again), or reject the order."""
    _order_owner_check(order, "seller")
    pay = frappe.db.get_value("PaymentTransaction", {"customer_order": order, "status": "Pending"}, "name")
    if decision == "verify":
        if not pay:
            frappe.throw(_("There is no payment proof to verify."))
        frappe.db.set_value("PaymentTransaction", pay, {"status": "Verified", "verified_by": frappe.session.user, "verified_on": frappe.utils.nowdate()})
        return _move(order, "Accepted", "seller")
    if decision == "reupload":
        if pay:
            frappe.db.set_value("PaymentTransaction", pay, {"status": "Rejected", "rejection_reason": reason or ""})
        return _move(order, "Awaiting Payment", "seller")
    if decision == "reject":
        if pay:
            frappe.db.set_value("PaymentTransaction", pay, {"status": "Rejected", "rejection_reason": reason or ""})
        if not (reason or "").strip():
            frappe.throw(_("Please give a reason."))
        return _move(order, "Rejected", "seller", reason)
    frappe.throw(_("Unknown decision."))


@frappe.whitelist()
def buyer_cancel(order, reason=None):
    buyer = _buyer_from_session()
    if frappe.db.get_value("Customer Order", order, "buyer_profile") != buyer:
        frappe.throw(_("Not your order."), frappe.PermissionError)
    return _move(order, "Cancelled", "buyer", reason)


@frappe.whitelist()
def seller_set_status(order, to):
    """Accepted → Packed → Dispatched → Delivered, by the shop."""
    return _move(order, to, "seller")


@frappe.whitelist()
def seller_orders(profile_id, status=None, limit=50, offset=0):
    """Incoming customer orders for one shop (no buyer's contact here; the detail view gives it, for this order only)."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(profile_id)
    f = {"seller_profile": profile_id}
    if status:
        f["status"] = status
    return frappe.get_all("Customer Order", filters=f,
                          fields=["name", "status", "total", "reserved_until", "creation", "buyer_profile"],
                          order_by="creation desc", limit_page_length=int(limit), limit_start=int(offset))
"""Customer orders, step 5: read APIs for the shop panel and the buyer's pages. Appended to trading/api/customer_payment.py
on dev-b (branch feat/customer-orders). No trade price or margin is returned to either side."""
import frappe
from frappe import _

from book_marketplace.trading.api.customer_order import _buyer_from_session


@frappe.whitelist()
def seller_order_detail(order):
    """One order for its shop: items, payment proofs, and the buyer's contact for this order only."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    doc = frappe.get_doc("Customer Order", order)
    _assert_profile_owner(doc.seller_profile)
    buyer = frappe.db.get_value("BuyerProfile", doc.buyer_profile, ["full_name", "phone", "email"], as_dict=True) or {}
    items = [{"book": i.book, "title": frappe.db.get_value("Book", i.book, "title"), "qty": i.qty,
              "option_label": i.get("option_label") or "",
              "unit_price": i.unit_price, "line_total": i.line_total} for i in doc.items]
    pays = frappe.get_all("PaymentTransaction", filters={"customer_order": order},
                          fields=["name", "amount", "payment_method", "reference_no", "payment_date", "status", "rejection_reason"],
                          order_by="creation desc")
    from book_marketplace.trading.api.customer_order import delivery_view
    return {"name": doc.name, "status": doc.status, "total": doc.total, "reserved_until": doc.reserved_until,
            "buyer": buyer, "items": items, "payments": pays, "buyer_note": doc.buyer_note,
            "delivery": delivery_view(doc),
            # print: the shop own number (also the PDF file name), the date and the shop letterhead
            "seller_doc_no": doc.get("seller_doc_no") or "", "creation": str(doc.creation)[:19],
            "dispatched_on": str(doc.get("dispatched_on") or "")[:19], "seller": _seller_party(doc.seller_profile)}


@frappe.whitelist()
def my_orders():
    """The buyer's orders, grouped by basket. Each order shows its shop's name only."""
    buyer = _buyer_from_session()
    rows = frappe.get_all("Customer Order", filters={"buyer_profile": buyer},
                          fields=["name", "basket", "seller_profile", "status", "total", "reserved_until", "creation"],
                          order_by="creation desc", limit_page_length=200)
    for r in rows:
        r["business_name"] = frappe.db.get_value("BusinessProfile", r.seller_profile, "business_name")
        r.pop("seller_profile", None)
    return rows


@frappe.whitelist()
def my_order_detail(order):
    """One of the buyer's orders, with the payment details of that shop only (for this order)."""
    buyer = _buyer_from_session()
    doc = frappe.get_doc("Customer Order", order)
    if doc.buyer_profile != buyer:
        frappe.throw(_("Not your order."), frappe.PermissionError)
    bank = frappe.get_all("BusinessBankAccount", filters={"business_profile": doc.seller_profile, "is_active": 1},
                          fields=["bank_name", "account_title", "account_number", "iban", "payment_instructions"],
                          order_by="is_default desc", limit_page_length=3)
    items = [{"title": frappe.db.get_value("Book", i.book, "title"), "qty": i.qty, "unit_price": i.unit_price,
              "line_total": i.line_total} for i in doc.items]
    from book_marketplace.trading.api.customer_order import delivery_view
    return {"name": doc.name, "status": doc.status, "total": doc.total, "reserved_until": doc.reserved_until,
            "business_name": frappe.db.get_value("BusinessProfile", doc.seller_profile, "business_name"),
            "items": items, "payment_accounts": bank, "delivery": delivery_view(doc)}
"""Customer orders v2 payment side (dev-b): manual transitions, shop confirmation (credit or pay on pickup, discount chain),
credit into khata, and revive for the seller. Appended to trading/api/customer_payment.py."""
import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from book_marketplace.trading.api.customer_order import revive_order as _revive

# manual orders (no proof): the shop confirms; expiry and cancel as before
TRANSITIONS.update({
    ("Awaiting Shop Confirmation", "Accepted"): "seller",
    ("Awaiting Shop Confirmation", "Rejected"): "seller",
    ("Awaiting Shop Confirmation", "Cancelled"): "buyer",
    ("Awaiting Shop Confirmation", "Expired"): "system",
})


def _apply_discount_chain(total, chain):
    """40,10,5 means successive discounts: each one on what is left after the previous one."""
    value = flt(total)
    steps = []
    for part in (chain or "").replace("+", ",").split(","):
        part = part.strip().rstrip("%")
        if not part:
            continue
        pct = flt(part)
        if pct < 0 or pct > 100:
            frappe.throw(_("Each discount must be between 0 and 100."))
        value = value * (100 - pct) / 100
        steps.append(pct)
    return round(value), steps


@frappe.whitelist()
def seller_confirm(order, mode, discount_chain=None, received_amount=None, difference=None):
    """Shop confirms a manual order. mode: 'credit' (ادھار, one month, into khata) or 'pay_on_pickup' (وصولی پر ادائیگی).
    discount_chain: successive discounts, e.g. '40,10,5'.
    received_amount: the round figure the shop actually got now (e.g. 15300 for a payable 15355).
    If it is below the payable amount, `difference` says what the gap is: 'discount' (رعایت) or 'balance' (بقایا).
    The customer never sees the chain, the rounding or 'credit'."""
    from book_marketplace.auth.api.auth import _assert_profile_owner

    doc = frappe.get_doc("Customer Order", order)
    _assert_profile_owner(doc.seller_profile)
    if mode not in ("credit", "pay_on_pickup"):
        frappe.throw(_("Unknown confirmation."))

    total, chain = flt(doc.subtotal), ""
    if discount_chain is not None:
        total, steps = _apply_discount_chain(doc.subtotal, discount_chain)
        chain = ",".join(str(int(s)) for s in steps)

    received, rounding = None, 0
    if received_amount not in (None, ""):
        received = flt(received_amount)
        if received < 0:
            frappe.throw(_("The amount received cannot be negative."))
        if received > total:
            frappe.throw(_("The amount received ({0}) is more than the payable amount ({1}).").format(received, total))
        gap = total - received
        if gap > 0:
            if difference == "discount":
                rounding = gap
                total = received
            elif difference != "balance":
                frappe.throw(_("The difference of {0} must be marked as discount or as balance.").format(gap))
    owed = total - (received or 0)

    frappe.db.set_value("Customer Order", order, {
        "discount_chain": chain,
        "total": total,
        "amount_received": received or 0,
        "rounding_discount": rounding,
        "payment_status": "Paid" if owed <= 0 else ("Credit" if mode == "credit" else "Due"),
        "credit_due_on": add_days(nowdate(), 30) if (mode == "credit" and owed > 0) else None,
    })
    res = _move(order, "Accepted", "seller")

    buyer_name = frappe.db.get_value("BuyerProfile", doc.buyer_profile, "full_name") or doc.buyer_profile

    def khata(entry_type, amount, notes):
        frappe.get_doc({
            "doctype": "Khata Entry", "business_profile": doc.seller_profile, "party_name": buyer_name,
            "entry_date": nowdate(), "entry_type": entry_type, "amount": amount, "reference": order, "notes": notes,
        }).insert(ignore_permissions=True)

    if mode == "credit":
        if total > 0:
            khata("Credit Given", total, "Customer order on credit, due in one month")
        if received:
            khata("Payment Received", received, "Paid at confirmation of a customer order")
    elif owed > 0:
        khata("Credit Given", owed, "Balance (بقایا) from a customer order paid on pickup")
    return res


@frappe.whitelist()
def revive(order):
    """Revive an expired order under the same number (buyer or shop). Stock must be available."""
    return _revive(order)


def _seller_party(profile_id):
    from book_marketplace.trading.api.invoice import _party
    return _party(profile_id)
