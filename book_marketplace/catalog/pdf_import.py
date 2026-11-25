"""Read a PDF catalog and turn it into book rows.

Flow: preview_pdf_catalog reads the PDF and returns rows (nothing is saved).
After review, create_books_from_rows saves the rows as Book records for one publisher.
"""
import json
import re

import frappe
from frappe import _
from frappe.utils import flt

LANGUAGES = ["Urdu", "English", "Arabic", "Roman Urdu", "Sindhi", "Pashto", "Punjabi", "Balochi", "Other"]
ISBN_RE = re.compile(r"(97[89][\d\- ]{9,13}\d|\b\d{9}[\dXx]\b)")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_TEXT_CHARS = 60000


def _extract_pdf_text(file_url):
    from pypdf import PdfReader

    file_doc = frappe.get_doc("File", {"file_url": file_url})
    reader = PdfReader(file_doc.get_full_path())
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _rule_rows(text):
    """Rule-based fallback: each line with an ISBN becomes a row; the title is the text around it."""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        match = ISBN_RE.search(line)
        if not match:
            continue
        isbn = re.sub(r"[^\dXx]", "", match.group(1)).upper()
        title = (line[: match.start()] + " " + line[match.end():]).strip(" -|:,")
        rows.append({"title": title or isbn, "author": "", "isbn": isbn, "mrp": 0, "language": "Urdu", "source": "rules"})
    return rows


def _ai_settings():
    settings = frappe.get_single("AI Import Settings")
    if not settings.enabled:
        return None
    key = settings.get_password("api_key", raise_exception=False)
    if not key:
        return None
    return {"key": key, "model": settings.model or "claude-haiku-5-5"}


def _ai_rows(text, cfg):
    import requests

    prompt = (
        "Extract every book from this catalog text. Return ONLY a JSON array. Each item has the keys "
        "title (string), author (string), isbn (string), mrp (number), language (one of "
        + ", ".join(LANGUAGES)
        + "). Use an empty string for unknown text and 0 for unknown prices. Do not invent ISBNs.\n\n"
        + text[:MAX_TEXT_CHARS]
    )
    response = requests.post(
        ANTHROPIC_URL,
        headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": cfg["model"], "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    response.raise_for_status()
    content = "".join(block.get("text", "") for block in response.json().get("content", []))
    match = re.search(r"\[.*\]", content, re.S)
    if not match:
        raise ValueError("Model did not return a JSON array.")
    rows = json.loads(match.group(0))
    for row in rows:
        row["source"] = "ai"
    return rows


def _clean(row):
    language = row.get("language") if row.get("language") in LANGUAGES else "Other"
    isbn = re.sub(r"[^\dXx]", "", str(row.get("isbn") or "")).upper()
    return {
        "title": (row.get("title") or "").strip()[:140],
        "author": (row.get("author") or "").strip()[:140],
        "isbn": isbn,
        "mrp": flt(row.get("mrp")),
        "language": language,
        "source": row.get("source", "rules"),
    }


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@frappe.whitelist()
def preview_pdf_catalog(file_url, use_ai=1):
    """Read a PDF already uploaded to File and return rows for review. Saves nothing."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Login is required."), frappe.PermissionError)
    if not file_url or not str(file_url).lower().endswith(".pdf"):
        frappe.throw(_("Upload a PDF file first."))
    text = _extract_pdf_text(file_url)
    if not text.strip():
        frappe.throw(_("No readable text found in this PDF. Scanned PDFs need OCR first."))

    cfg = _ai_settings() if _as_int(use_ai) else None
    warning = None
    rows = []
    if cfg:
        try:
            rows = _ai_rows(text, cfg)
        except Exception as exc:  # fall back to rules and report why
            warning = f"AI parsing failed, used rules instead: {exc}"
            frappe.log_error(title="PDF catalog AI parse", message=str(exc))
    if not rows:
        rows = _rule_rows(text)
    rows = [_clean(r) for r in rows if (r.get("title") or r.get("isbn"))]
    return {"rows": rows, "count": len(rows), "used_ai": bool(cfg) and not warning, "warning": warning}


@frappe.whitelist()
def create_books_from_rows(rows, publisher):
    """Save reviewed rows as Book records for one publisher. Skips ISBNs that already exist."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Login is required."), frappe.PermissionError)
    owner = frappe.db.get_value("BusinessProfile", publisher, "user")
    if not owner:
        frappe.throw(_("Publisher profile not found."))
    if owner != frappe.session.user and "System Manager" not in frappe.get_roles():
        from book_marketplace.auth.permissions import assert_access
        assert_access(publisher, "catalog.edit")

    if isinstance(rows, str):
        rows = json.loads(rows)
    created, skipped = [], []
    for raw in rows:
        row = _clean(raw)
        if not row["title"]:
            skipped.append({"title": "", "reason": "no title"})
            continue
        if row["isbn"] and frappe.db.exists("Book", {"isbn": row["isbn"]}):
            skipped.append({"title": row["title"], "reason": "ISBN exists"})
            continue
        book = frappe.get_doc({
            "doctype": "Book",
            "title": row["title"],
            "author": row["author"],
            "publisher": publisher,
            "isbn": row["isbn"] or None,
            "language": row["language"],
            "book_type": "Single Volume",
            "set_mrp": row["mrp"],
            "global_visibility": 1,
        })
        book.insert(ignore_permissions=True)
        created.append(book.name)
    frappe.db.commit()
    return {"created": created, "skipped": skipped}
