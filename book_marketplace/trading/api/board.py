"""Message board.
Public board: approved shops post notices (text and an image). Everyone can read open posts; a signed-in shop can
report a post, and an admin (System Manager) hides or shows it. Posts with price talk are hidden automatically.
Announcements: an admin sends a message to every shop; each shop sees it in its panel and marks it read."""
import frappe
from frappe import _
from frappe.utils import now_datetime

from book_marketplace.auth.api.auth import _assert_profile_owner
from book_marketplace.trading.api.policy import PRICE_PATTERN

MAX_POST = 1000
MAX_ANNOUNCEMENT = 2000


def _is_admin():
    return "System Manager" in frappe.get_roles()


def _approved_shop(profile_id):
    _assert_profile_owner(profile_id)
    status = frappe.db.get_value("BusinessProfile", profile_id, "status")
    if status != "Approved":
        frappe.throw(_("Only an approved shop can post on the board."), frappe.PermissionError)


def _text(value, limit, empty_msg):
    text = (value or "").strip()
    if not text:
        frappe.throw(empty_msg)
    if len(text) > limit:
        frappe.throw(_("Text is too long ({0} characters at most).").format(limit))
    return text


@frappe.whitelist()
def create_post(profile_id, body, image=None):
    _approved_shop(profile_id)
    text = _text(body, MAX_POST, _("Write the post first."))
    hidden = bool(PRICE_PATTERN.search(text))
    doc = frappe.get_doc({
        "doctype": "Shop Board Post", "business_profile": profile_id, "body": text,
        "image": image or None, "status": "Hidden" if hidden else "Open",
        "hidden_reason": "Price talk" if hidden else None, "reports": 0, "created_on": now_datetime(),
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def list_posts(limit=30, offset=0):
    """Open posts, newest first, with the shop's name and city."""
    limit = min(frappe.utils.cint(limit) or 30, 100)
    rows = frappe.get_all(
        "Shop Board Post", filters={"status": "Open"},
        fields=["name", "business_profile", "body", "image", "created_on"],
        order_by="created_on desc", limit_start=frappe.utils.cint(offset), limit_page_length=limit,
    )
    info = {p.name: p for p in frappe.get_all("BusinessProfile", filters={"name": ["in", list({r.business_profile for r in rows} or [""])]},
                                              fields=["name", "business_name", "city"])}
    for r in rows:
        r["business_name"] = (info.get(r.business_profile) or frappe._dict()).get("business_name") or r.business_profile
        r["city"] = (info.get(r.business_profile) or frappe._dict()).get("city") or ""
        r["created_on"] = str(r.created_on or "")
    return rows


@frappe.whitelist()
def report_post(post, profile_id, reason=""):
    _approved_shop(profile_id)
    doc = frappe.get_doc("Shop Board Post", post)
    if doc.business_profile == profile_id:
        frappe.throw(_("You cannot report your own post."))
    if frappe.db.exists("Board Report", {"post": post, "reporter_profile": profile_id}):
        frappe.throw(_("You have already reported this post."))
    frappe.get_doc({"doctype": "Board Report", "post": post, "reporter_profile": profile_id,
                    "reason": (reason or "").strip()[:500], "created_on": now_datetime()}).insert(ignore_permissions=True)
    doc.reports = (doc.reports or 0) + 1
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "reports": doc.reports}


@frappe.whitelist()
def admin_posts(status=None):
    """Admin only: all posts with their report counts (to hide or show them)."""
    if not _is_admin():
        frappe.throw(_("Only an admin can see this."), frappe.PermissionError)
    filters = {"status": status} if status else {}
    return frappe.get_all("Shop Board Post", filters=filters,
                          fields=["name", "business_profile", "body", "status", "reports", "hidden_reason", "created_on"],
                          order_by="reports desc, created_on desc", limit_page_length=200)


@frappe.whitelist()
def set_post_status(post, status):
    if not _is_admin():
        frappe.throw(_("Only an admin can hide or show a post."), frappe.PermissionError)
    if status not in ("Open", "Hidden"):
        frappe.throw(_("Status must be Open or Hidden."))
    doc = frappe.get_doc("Shop Board Post", post)
    doc.status = status
    doc.hidden_reason = "Hidden by admin" if status == "Hidden" else None
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def am_i_admin():
    return {"admin": _is_admin()}


@frappe.whitelist()
def send_announcement(title, body):
    """Admin only: a message to every shop."""
    if not _is_admin():
        frappe.throw(_("Only an admin can send an announcement."), frappe.PermissionError)
    title = _text(title, 140, _("Write the title."))
    text = _text(body, MAX_ANNOUNCEMENT, _("Write the message."))
    doc = frappe.get_doc({"doctype": "Admin Announcement", "title": title, "body": text,
                          "created_on": now_datetime(), "created_by": frappe.session.user}).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def my_announcements(profile_id, limit=30):
    _assert_profile_owner(profile_id)
    rows = frappe.get_all("Admin Announcement", fields=["name", "title", "body", "created_on"],
                          order_by="created_on desc", limit_page_length=frappe.utils.cint(limit) or 30)
    read = set(frappe.get_all("Announcement Read", filters={"business_profile": profile_id,
                                                            "announcement": ["in", [r.name for r in rows] or [""]]},
                              pluck="announcement"))
    for r in rows:
        r["read"] = r.name in read
        r["created_on"] = str(r.created_on or "")
    return rows


@frappe.whitelist()
def mark_announcement_read(announcement, profile_id):
    _assert_profile_owner(profile_id)
    if not frappe.db.exists("Announcement Read", {"announcement": announcement, "business_profile": profile_id}):
        frappe.get_doc({"doctype": "Announcement Read", "announcement": announcement,
                        "business_profile": profile_id, "read_on": now_datetime()}).insert(ignore_permissions=True)
    return {"ok": True}
