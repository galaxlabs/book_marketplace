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
            _release(doc.seller_profile, it.book, int(it.qty))
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
