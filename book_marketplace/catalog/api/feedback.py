import frappe
from frappe import _

TYPES = ("Suggestion", "Problem", "Other")


@frappe.whitelist(allow_guest=True)
def submit_feedback(message, feedback_type="Suggestion", sender_name=None, contact=None, page_url=None):
    """A visitor's feedback, shown to the admin in the Frappe desk (Site Feedback)."""
    message = (message or "").strip()
    if len(message) < 5:
        frappe.throw(_("Please write at least 5 characters."))
    if len(message) > 3000:
        frappe.throw(_("Message is too long (3000 characters at most)."))
    if feedback_type not in TYPES:
        feedback_type = "Other"
    doc = frappe.get_doc({
        "doctype": "Site Feedback",
        "message": message,
        "feedback_type": feedback_type,
        "sender_name": (sender_name or "").strip()[:140] or None,
        "contact": (contact or "").strip()[:140] or None,
        "page_url": (page_url or "").strip()[:500] or None,
        "status": "Open",
    }).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "received"}
