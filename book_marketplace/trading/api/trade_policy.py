"""Publisher trade discount policies: the rate a shop gets when it buys from a publisher.

A policy belongs to one publisher. It applies to the shops it covers (all shops except the listed ones, or only the
listed ones) while Status is Active and today is inside its valid dates. Each policy has steps (tiers):
- Condition: Bill only (this order's amount), Monthly only (the shop's purchases from this publisher in the calendar
  month so far), Both, or Either.
- Replace: the step's rate replaces the normal rate when it is higher. Add on top: its rate is added to the normal rate.

The highest-priority policy that has at least one step met is used for the order (one policy per order).
Effective rate = max(normal rate, highest Replace rate met) + sum of Add-on-top rates met, capped at 100.
Monthly amounts count orders the publisher has accepted or later (Accepted, Dispatched, Awaiting Agreement,
Received, Short) in the same calendar month. A policy change counts for orders from then on; orders already made keep
their price.
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate, get_first_day

COUNTED = ("Accepted", "Dispatched", "Awaiting Agreement", "Received", "Short")


def month_purchases(shop, publisher, today=None):
    """What the shop has bought from this publisher this calendar month (accepted orders)."""
    today = getdate(today or nowdate())
    rows = frappe.get_all("Purchase Order Request",
                          filters={"buyer_profile": shop, "seller_profile": publisher,
                                   "status": ["in", list(COUNTED)],
                                   "creation": [">=", get_first_day(today)]},
                          fields=["total"], limit_page_length=0)
    return sum(flt(r.total) for r in rows)


def covered_shops(publisher):
    """Shops that buy from or list this publisher's books (they are told about its policies)."""
    buyers = frappe.db.sql("select distinct buyer_profile from `tabPurchase Order Request` where seller_profile = %s", publisher)
    holders = frappe.db.sql("""select distinct c.business_profile from `tabBusinessCatalogItem` c
        join `tabBook` b on b.name = c.book where b.publisher = %s and c.business_profile != %s""", (publisher, publisher))
    return sorted({r[0] for r in buyers + holders if r[0] and r[0] != publisher})


def announce_policy(policy_name, event):
    """event: 'issued', 'stopped' or 'expired'. Notes for the covered shops and one board notice."""
    from book_marketplace.trading.api.notifications import notify
    doc = frappe.get_doc("Trade Discount Policy", policy_name)
    texts = {
        "issued": ("New discount policy: {0}", "Publisher {0} has a new discount policy, {1}. See Trade discounts for the steps."),
        "stopped": ("Discount policy ended: {0}", "Publisher {0} has ended the discount policy {1}. Orders from now use the normal rate."),
        "expired": ("Discount policy ended: {0}", "The discount policy {1} of publisher {0} has reached its end date."),
    }
    tt, bb = texts[event]
    pub = frappe.db.get_value("BusinessProfile", doc.publisher, "business_name") or doc.publisher
    title, body = tt.format(doc.policy_name), bb.format(pub, doc.policy_name)
    for shop in covered_shops(doc.publisher):
        notify(shop, title, body, link="/discount-policies", kind="policy")
    frappe.get_doc({"doctype": "Shop Board Post", "business_profile": doc.publisher, "status": "Open",
                    "body": title + " — " + body, "reports": 0, "created_on": frappe.utils.now_datetime()}).insert(ignore_permissions=True)


def expire_policies():
    """Daily: an active policy past its end date stops, and the shops hear about it."""
    today = getdate(nowdate())
    for p in frappe.get_all("Trade Discount Policy", filters={"status": "Active", "valid_to": ["<", today]}, pluck="name"):
        frappe.db.set_value("Trade Discount Policy", p, "status", "Stopped")
        announce_policy(p, "expired")


def _covers(policy, shop):
    listed = {s.business_profile for s in policy.get("shops") or []}
    return (shop not in listed) if policy.applies_to_all else (shop in listed)


def _active_on(policy, today):
    if policy.status != "Active":
        return False
    if policy.valid_from and getdate(policy.valid_from) > today:
        return False
    if policy.valid_to and getdate(policy.valid_to) < today:
        return False
    return True


def _tier_met(tier, bill, monthly):
    hit_bill = flt(bill) >= flt(tier.min_bill)
    hit_month = flt(monthly) >= flt(tier.min_monthly)
    if tier.basis == "Bill only":
        return hit_bill
    if tier.basis == "Monthly only":
        return hit_month
    if tier.basis == "Both":
        return hit_bill and hit_month
    return hit_bill or hit_month  # Either


def rate_for(publisher, shop, bill_amount, normal_percent=0, today=None):
    """The rate for one order: {rate, policy, steps_met, normal}. No policy: the normal rate."""
    today = getdate(today or nowdate())
    monthly = month_purchases(shop, publisher, today)
    policies = frappe.get_all("Trade Discount Policy", filters={"publisher": publisher, "status": "Active"},
                              fields=["name", "policy_name", "priority", "status", "valid_from", "valid_to", "applies_to_all"],
                              order_by="priority desc, creation asc", limit_page_length=0)
    for p in policies:
        doc = frappe.get_doc("Trade Discount Policy", p.name)
        if not (_active_on(doc, today) and _covers(doc, shop)):
            continue
        met = [t for t in doc.tiers if _tier_met(t, bill_amount, monthly)]
        if not met:
            continue
        replace = max([flt(t.percent) for t in met if t.mode == "Replace"] or [0])
        add = sum(flt(t.percent) for t in met if t.mode == "Add on top")
        rate = min(max(flt(normal_percent), replace) + add, 100)
        return {"rate": rate, "policy": doc.name, "policy_name": doc.policy_name, "normal": flt(normal_percent),
                "steps_met": [{"basis": t.basis, "percent": flt(t.percent), "mode": t.mode} for t in met],
                "monthly": monthly}
    return {"rate": flt(normal_percent), "policy": None, "policy_name": None, "normal": flt(normal_percent),
            "steps_met": [], "monthly": monthly}


@frappe.whitelist()
def preview_rate(publisher, shop, bill_amount, normal_percent=0):
    """What a shop would get from this publisher for a bill of this amount (no order is made)."""
    from book_marketplace.auth.api.auth import _assert_profile_owner
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    _assert_profile_owner(publisher)
    return rate_for(publisher, shop, flt(bill_amount), flt(normal_percent))


@frappe.whitelist()
def my_policies(publisher):
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(publisher)
    rows = frappe.get_all("Trade Discount Policy", filters={"publisher": publisher},
                          fields=["name", "policy_name", "status", "priority", "valid_from", "valid_to", "applies_to_all"],
                          order_by="priority desc, creation asc", limit_page_length=0)
    for r in rows:
        doc = frappe.get_doc("Trade Discount Policy", r.name)
        r["steps"] = [{"basis": t.basis, "min_bill": flt(t.min_bill), "min_monthly": flt(t.min_monthly),
                       "percent": flt(t.percent), "mode": t.mode} for t in doc.tiers]
        r["shops"] = [{"business_profile": s.business_profile, "note": s.note or ""} for s in doc.shops]
    return rows


@frappe.whitelist()
def save_policy(publisher, policy_name, status="Draft", priority=0, valid_from=None, valid_to=None,
                applies_to_all=1, steps=None, shops=None, name=None):
    """Create or change a policy. steps: JSON list of {basis, min_bill, min_monthly, percent, mode};
    shops: JSON list of {business_profile, note} (excluded when applies_to_all, included otherwise)."""
    import json
    from book_marketplace.auth.api.auth import _assert_profile_owner
    _assert_profile_owner(publisher)
    if status not in ("Draft", "Active", "Stopped"):
        frappe.throw(_("Status must be Draft, Active or Stopped."))
    step_rows = json.loads(steps) if isinstance(steps, str) else (steps or [])
    if not step_rows:
        frappe.throw(_("Add at least one discount step."))
    for s in step_rows:
        if not (0 <= flt(s.get("percent")) <= 100):
            frappe.throw(_("A discount must be between 0 and 100 percent."))
    shop_rows = json.loads(shops) if isinstance(shops, str) else (shops or [])
    for s in shop_rows:
        if not frappe.db.exists("BusinessProfile", s.get("business_profile")):
            frappe.throw(_("Shop not found: {0}").format(s.get("business_profile")))
    if valid_from and valid_to and getdate(valid_to) < getdate(valid_from):
        frappe.throw(_("The end date is before the start date."))
    data = {
        "policy_name": (policy_name or "").strip()[:140], "publisher": publisher, "status": status,
        "priority": int(priority or 0), "valid_from": valid_from or None, "valid_to": valid_to or None,
        "applies_to_all": 1 if str(applies_to_all) in ("1", "true", "True") else 0,
        "tiers": [{"basis": s.get("basis") or "Bill only", "min_bill": flt(s.get("min_bill")),
                   "min_monthly": flt(s.get("min_monthly")), "percent": flt(s.get("percent")),
                   "mode": s.get("mode") or "Replace"} for s in step_rows],
        "shops": [{"business_profile": s["business_profile"], "note": (s.get("note") or "")[:140]} for s in shop_rows],
    }
    if not data["policy_name"]:
        frappe.throw(_("Write the policy name."))
    old_status = None
    if name:
        doc = frappe.get_doc("Trade Discount Policy", name)
        if doc.publisher != publisher:
            frappe.throw(_("This policy is not yours."), frappe.PermissionError)
        old_status = doc.status
        doc.update(data)
        doc.set("tiers", [])
        doc.set("shops", [])
        for t in data["tiers"]:
            doc.append("tiers", t)
        for s in data["shops"]:
            doc.append("shops", s)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "Trade Discount Policy", **data}).insert(ignore_permissions=True)
    if doc.status != old_status and doc.status in ("Active", "Stopped"):
        announce_policy(doc.name, "issued" if doc.status == "Active" else "stopped")
    return {"name": doc.name, "status": doc.status}
