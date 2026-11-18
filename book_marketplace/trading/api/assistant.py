"""AI book assistant. The key lives in 'Kitab AI Settings' (Password field), never in code or logs."""
import frappe
import requests
from frappe import _
from frappe.utils import today

API_URL = "https://api.anthropic.com/v1/messages"
DAILY_LIMIT = 30
MAX_QUESTION = 1000
DEFAULT_INSTRUCTIONS = (
    "You are the book assistant of Kitab Bazaar. Answer briefly in simple Urdu (English terms in brackets when useful). "
    "Help the user find books, subjects, authors and shops. Never invent prices, stock or shop names; "
    "if you do not know, say so."
)


@frappe.whitelist()
def ask_assistant(question, book_id=None):
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    q = (question or "").strip()
    if not q:
        frappe.throw(_("Write a question first."))
    if len(q) > MAX_QUESTION:
        frappe.throw(_("Question is too long (max {0} characters).").format(MAX_QUESTION))

    s = frappe.get_single("Kitab AI Settings")
    if not s.enabled:
        frappe.throw(_("The AI assistant is off."))
    key = s.get_password("api_key", raise_exception=False)
    if not key:
        frappe.throw(_("The AI assistant key is not set yet."))

    cache_key = f"kitab_ai:{user}:{today()}"
    used = int(frappe.cache().get_value(cache_key) or 0)
    if used >= DAILY_LIMIT:
        frappe.throw(_("Daily limit reached ({0} questions). Try tomorrow.").format(DAILY_LIMIT))
    frappe.cache().set_value(cache_key, used + 1, expires_in_sec=86400)

    context = ""
    if book_id and frappe.db.exists("Book", book_id):
        b = frappe.db.get_value("Book", book_id, ["title", "author", "language", "set_mrp"], as_dict=True)
        context = f"\nThe user is looking at the book: {b.title} by {b.author} (language: {b.language}, MRP: {b.set_mrp})."

    body = {
        "model": s.model or "claude-sonnet-5-5",
        "max_tokens": 600,
        "system": (s.instructions or DEFAULT_INSTRUCTIONS) + context,
        "messages": [{"role": "user", "content": q}],
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    try:
        res = requests.post(API_URL, headers=headers, json=body, timeout=30)
    except requests.RequestException:
        frappe.throw(_("The AI service could not be reached. Try again."))
    if res.status_code != 200:
        frappe.log_error(title="Kitab AI error", message=f"status {res.status_code}")
        frappe.throw(_("The AI assistant is busy. Try again later."))
    data = res.json()
    text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
    return {"answer": text.strip(), "remaining_today": DAILY_LIMIT - used - 1}
