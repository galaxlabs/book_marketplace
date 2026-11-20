"""Shop policy: restrictions for shortage and for dispute abuse, appeals, ratings, the verified badge, and feedback.

Defaults below are the first reading of the owner's rules. Each is one constant, so the owner can change it
in one place. Open questions are listed in ref/WORK-NOTEBOOK.md.
- Shortage: among a shop's last 14 received orders counted since its last shortage restriction, 12 or more were
  short -> restriction.
- Dispute: once a shop has 10 or more orders at receipt stage (counted since its last dispute restriction), 60% or
  more were disputed by the seller -> restriction.
- Ladder: 1, 2, 4, 8, then 16 months. Each new restriction of the same kind moves one step up.
- A restricted shop cannot place new orders until the restriction ends or an admin accepts its appeal.
- Verified badge: 10 or more closed orders, average rating 4 or more from at least 5 ratings, no open report.
  A rating from a verified rater counts double in the weighted average.
- Reports are visible only to admins. A shop with an open report cannot get the badge.
- Comments are hidden automatically when they talk about price or contain a word from ABUSIVE_WORDS.
"""
import re

import frappe
from frappe import _
from frappe.utils import add_months, flt, now_datetime

from book_marketplace.auth.api.auth import _assert_profile_owner

SHORT_AFTER = 10  # shortage rule applies once a shop has this many received orders (since the last restriction)
SHORT_SHARE = 0.60
DISPUTE_AFTER = 10
DISPUTE_SHARE = 0.60
LADDER_MONTHS = [1, 2, 4, 8, 16]
# The verified badge is a paid premium service. Its rule (plan, price, who grants it) is not decided yet,
# so no shop gets the badge automatically. Ratings and their average still show.
VERIFIED_BADGE_ENABLED = False
PRICE_PATTERN = re.compile(r"(\bpkr\b|\brs\.?\b|\bprice\b|روپے|روپیہ|قیمت)", re.IGNORECASE)
ABUSIVE_WORDS = []  # the owner supplies the list; empty means only the price rule is active
MAX_COMMENT = 500


def _profile_of_user(profile_id):
    _assert_profile_owner(profile_id)


def _is_admin():
    return "System Manager" in frappe.get_roles()


def _active(profile):
    return frappe.get_all(
        "Shop Restriction",
        filters={"business_profile": profile, "status": "Active", "ends_on": [">", now_datetime()]},
        fields=["name", "kind", "ends_on"],
        order_by="ends_on desc",
        limit_page_length=1,
    )


def assert_can_order(profile):
    """Called before a shop places an order. A restricted shop cannot order until the restriction ends."""
    rows = _active(profile)
    if rows:
        frappe.throw(_("Ordering is paused until {0} ({1} restriction).").format(
            str(rows[0].ends_on)[:16], rows[0].kind.lower()))


def _last_restriction_start(profile, kind):
    return frappe.db.get_value("Shop Restriction", {"business_profile": profile, "kind": kind},
                               "starts_on", order_by="starts_on desc")


def _impose(profile, kind, reason, sources):
    previous = frappe.db.count("Shop Restriction", {"business_profile": profile, "kind": kind})
    level = min(previous + 1, len(LADDER_MONTHS))
    start = now_datetime()
    doc = frappe.get_doc({
        "doctype": "Shop Restriction", "business_profile": profile, "kind": kind, "level": level,
        "status": "Active", "starts_on": start, "ends_on": add_months(start, LADDER_MONTHS[level - 1]),
        "reason": reason, "appeal_status": "None", "source_orders": ", ".join(sources),
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def check_shortage(profile):
    """Shortage rule. Counts only orders received after the last shortage restriction.
    Applies when at least SHORT_AFTER orders were received and SHORT_SHARE of them came short."""
    if _active(profile):
        return None
    filters = {"buyer_profile": profile, "status": ["in", ["Received", "Short"]]}
    since = _last_restriction_start(profile, "Shortage")
    if since:
        filters["agreed_on"] = [">", since]
    rows = frappe.get_all("Purchase Order Request", filters=filters, fields=["name", "status"],
                          order_by="agreed_on desc", limit_page_length=0)
    if len(rows) < SHORT_AFTER:
        return None
    shorts = sum(1 for r in rows if r.status == "Short")
    if shorts / len(rows) < SHORT_SHARE:
        return None
    return _impose(profile, "Shortage", _("{0} of {1} orders were received short.").format(shorts, len(rows)),
                   [r.name for r in rows])


def check_dispute(profile):
    """Dispute rule. Counts only orders that reached the receipt stage after the last dispute restriction."""
    if _active(profile):
        return None
    filters = {"buyer_profile": profile, "receipt_submitted_on": ["is", "set"]}
    since = _last_restriction_start(profile, "Dispute")
    if since:
        filters["receipt_submitted_on"] = [">", since]
    rows = frappe.get_all("Purchase Order Request", filters=filters, fields=["name", "receipt_note"],
                          order_by="receipt_submitted_on desc", limit_page_length=0)
    if len(rows) < DISPUTE_AFTER:
        return None
    disputed = [r for r in rows if (r.receipt_note or "").strip()]
    if len(disputed) / len(rows) < DISPUTE_SHARE:
        return None
    return _impose(profile, "Dispute", _("{0} of {1} orders were disputed by the seller.").format(len(disputed), len(rows)),
                   [r.name for r in rows])


def after_receipt(profile):
    """Run after an agreement, a dispute or an expiry. Returns the restriction names created, if any."""
    made = []
    for check in (check_shortage, check_dispute):
        name = check(profile)
        if name:
            made.append(name)
    return made


@frappe.whitelist()
def my_restrictions(profile_id):
    _profile_of_user(profile_id)
    return frappe.get_all(
        "Shop Restriction", filters={"business_profile": profile_id},
        fields=["name", "kind", "level", "status", "starts_on", "ends_on", "reason",
                "appeal_status", "appeal_note", "appeal_response", "appeal_on"],
        order_by="starts_on desc", limit_page_length=50,
    )


@frappe.whitelist()
def submit_appeal(name, profile_id, note):
    """The shop appeals once. The admin decides."""
    _profile_of_user(profile_id)
    doc = frappe.get_doc("Shop Restriction", name)
    if doc.business_profile != profile_id:
        frappe.throw(_("Only the restricted shop can appeal."), frappe.PermissionError)
    if doc.status != "Active" or doc.ends_on <= now_datetime():
        frappe.throw(_("Only an active restriction can be appealed."))
    if (doc.appeal_status or "None") != "None":
        frappe.throw(_("This restriction has already been appealed."))
    text = (note or "").strip()
    if not text:
        frappe.throw(_("Write your appeal."))
    doc.appeal_status = "Pending"
    doc.appeal_note = text
    doc.appeal_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "appeal_status": doc.appeal_status}


@frappe.whitelist()
def decide_appeal(name, decision, response=""):
    """Admin only. Accepted: the restriction is lifted now. Rejected: the restriction stands."""
    if not _is_admin():
        frappe.throw(_("Only an admin decides appeals."), frappe.PermissionError)
    if decision not in ("Accepted", "Rejected"):
        frappe.throw(_("Decision must be Accepted or Rejected."))
    doc = frappe.get_doc("Shop Restriction", name)
    if doc.appeal_status != "Pending":
        frappe.throw(_("No appeal is waiting on this restriction."))
    doc.appeal_status = decision
    doc.appeal_response = (response or "").strip()
    if decision == "Accepted":
        doc.status = "Lifted"
        doc.ends_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status, "appeal_status": doc.appeal_status}


def end_restrictions():
    """Daily job: a restriction past its end date is marked Ended, so the shop can order again."""
    names = frappe.get_all("Shop Restriction", filters={"status": "Active", "ends_on": ["<=", now_datetime()]},
                           pluck="name")
    for n in names:
        frappe.db.set_value("Shop Restriction", n, "status", "Ended")
    return len(names)


# ---------- ratings and the verified badge ----------

def _closed_orders(profile):
    """Agreed orders the shop took part in, as buyer or as seller."""
    as_buyer = frappe.get_all("Purchase Order Request",
                              filters={"status": ["in", ["Received", "Short"]], "buyer_profile": profile},
                              pluck="name", limit_page_length=0)
    as_seller = frappe.get_all("Purchase Order Request",
                               filters={"status": ["in", ["Received", "Short"]], "seller_profile": profile},
                               pluck="name", limit_page_length=0)
    return len(as_buyer) + len(as_seller)


def _ratings_for(profile):
    return frappe.get_all("Shop Rating", filters={"business_profile": profile},
                          fields=["stars", "rater_verified"], limit_page_length=0)


def _is_verified(profile):
    if not VERIFIED_BADGE_ENABLED:
        return False  # paid premium badge: rule pending the owner's decision
    ratings = _ratings_for(profile)
    if _active(profile):
        return False
    if frappe.db.exists("Shop Feedback", {"business_profile": profile, "kind": "Report", "status": "Open"}):
        return False
    return True


def _reputation(profile):
    ratings = _ratings_for(profile)
    if not ratings:
        return {"count": 0, "average": None, "weighted_average": None, "verified": _is_verified(profile)}
    raw = sum(flt(r.stars) for r in ratings) / len(ratings)
    weights = [2 if r.rater_verified else 1 for r in ratings]
    weighted = sum(flt(r.stars) * w for r, w in zip(ratings, weights)) / sum(weights)
    return {"count": len(ratings), "average": round(raw, 2), "weighted_average": round(weighted, 2),
            "verified": _is_verified(profile)}


@frappe.whitelist()
def shop_reputation(business_profile):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return _reputation(business_profile)


@frappe.whitelist()
def rate_order(purchase_order, profile_id, stars, comment=""):
    """The buyer rates the seller once, after the receipt is agreed."""
    _profile_of_user(profile_id)
    po = frappe.get_doc("Purchase Order Request", purchase_order)
    if po.buyer_profile != profile_id:
        frappe.throw(_("Only the buyer rates this order."), frappe.PermissionError)
    if po.status not in ("Received", "Short"):
        frappe.throw(_("Rate the order after the receipt is agreed (now: {0}).").format(po.status))
    value = int(stars or 0)
    if value < 1 or value > 5:
        frappe.throw(_("Stars must be from 1 to 5."))
    if frappe.db.exists("Shop Rating", {"purchase_order": purchase_order}):
        frappe.throw(_("This order is already rated."))
    doc = frappe.get_doc({
        "doctype": "Shop Rating", "business_profile": po.seller_profile, "rater_profile": profile_id,
        "purchase_order": purchase_order, "stars": value, "comment": (comment or "").strip()[:MAX_COMMENT],
        "rater_verified": 1 if _is_verified(profile_id) else 0, "rated_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "reputation": _reputation(po.seller_profile)}


# ---------- comments, likes and reports ----------

def _hidden_reason(text):
    if PRICE_PATTERN.search(text):
        return "Price talk"
    low = text.lower()
    if any(w and w.lower() in low for w in ABUSIVE_WORDS):
        return "Abusive words"
    return ""


@frappe.whitelist()
def add_feedback(business_profile, kind, text="", profile_id=None):
    """Any logged-in user can comment, like or report a shop. Reports are seen only by admins."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    if profile_id:
        _profile_of_user(profile_id)
    if kind not in ("Comment", "Like", "Report"):
        frappe.throw(_("Kind must be Comment, Like or Report."))
    if not frappe.db.exists("BusinessProfile", business_profile):
        frappe.throw(_("Shop not found."))
    user = frappe.session.user
    if kind == "Like":
        existing = frappe.db.get_value("Shop Feedback", {"business_profile": business_profile, "kind": "Like",
                                                         "author_user": user}, "name")
        if existing:
            return {"name": existing, "hidden": False, "duplicate": True}
    body = (text or "").strip()[:MAX_COMMENT]
    if kind in ("Comment", "Report") and not body:
        frappe.throw(_("Write your comment." if kind == "Comment" else "Write why you are reporting this shop."))
    hidden_reason = _hidden_reason(body) if kind == "Comment" else ""
    doc = frappe.get_doc({
        "doctype": "Shop Feedback", "business_profile": business_profile, "kind": kind, "author_user": user,
        "author_profile": profile_id or None, "status": "Open", "text": body,
        "hidden": 1 if hidden_reason else 0, "hidden_reason": hidden_reason, "created_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "hidden": bool(hidden_reason), "hidden_reason": hidden_reason}


@frappe.whitelist()
def list_feedback(business_profile):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    user = frappe.session.user
    likes = frappe.get_all("Shop Feedback", filters={"business_profile": business_profile, "kind": "Like"},
                           fields=["author_user"], limit_page_length=0)
    comments = frappe.get_all(
        "Shop Feedback", filters={"business_profile": business_profile, "kind": "Comment", "hidden": 0},
        fields=["name", "text", "created_on", "author_user"], order_by="created_on desc", limit_page_length=100)
    out = {
        "likes": len(likes),
        "liked_by_me": any(l.author_user == user for l in likes),
        "comments": [{"name": c.name, "text": c.text, "created_on": str(c.created_on or ""),
                      "mine": c.author_user == user} for c in comments],
    }
    if _is_admin():
        out["reports"] = frappe.get_all(
            "Shop Feedback", filters={"business_profile": business_profile, "kind": "Report"},
            fields=["name", "text", "status", "author_user", "created_on"], order_by="created_on desc",
            limit_page_length=100)
        out["hidden_comments"] = frappe.get_all(
            "Shop Feedback", filters={"business_profile": business_profile, "kind": "Comment", "hidden": 1},
            fields=["name", "text", "hidden_reason"], limit_page_length=100)
    return out
