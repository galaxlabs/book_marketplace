"""Representatives: a business (publisher, distributor, agent or shop) appoints another business to visit its
debtors. The appointed business must accept. An accepted representative sees the appointing business's debtors
(from the khata links) and sends visit notices IN THE APPOINTING BUSINESS'S NAME. Notices go to debtors only,
as shop notifications; nothing is posted on a board.
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from book_marketplace.auth.api.auth import _assert_profile_owner
from book_marketplace.trading.api.dues import dues_summary
from book_marketplace.trading.api.notifications import notify


def _name(profile):
    return frappe.db.get_value("BusinessProfile", profile, "business_name") or profile


def _row(name):
    return frappe.db.get_value("Business Representative", name, ["principal", "representative", "status"], as_dict=True)


@frappe.whitelist()
def appoint(principal, representative, note=""):
    _assert_profile_owner(principal)
    if principal == representative:
        frappe.throw(_("A business cannot be its own representative."))
    if not frappe.db.exists("BusinessProfile", representative):
        frappe.throw(_("Business not found."))
    if frappe.db.exists("Business Representative", {"principal": principal, "representative": representative,
                                                     "status": ["in", ["Pending", "Active"]]}):
        frappe.throw(_("This business is already appointed or waiting to answer."))
    doc = frappe.get_doc({"doctype": "Business Representative", "principal": principal,
                          "representative": representative, "status": "Pending", "note": (note or "")[:300],
                          "appointed_on": now_datetime()}).insert(ignore_permissions=True)
    notify(representative, _("Representative request from {0}").format(_name(principal)),
           _("{0} asks you to be its representative to visit its debtors. Answer it in Representatives.").format(_name(principal)),
           link="/representatives", kind="representative")
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def respond(name, profile, accept=1):
    row = _row(name)
    if not row:
        frappe.throw(_("Not found."))
    _assert_profile_owner(row.representative)
    if profile != row.representative:
        frappe.throw(_("Only the representative answers."), frappe.PermissionError)
    if row.status != "Pending":
        frappe.throw(_("This request is already answered."))
    yes = str(accept) in ("1", "true", "True")
    frappe.db.set_value("Business Representative", name, {"status": "Active" if yes else "Ended", "answered_on": now_datetime()})
    notify(row.principal, _("Representative answer from {0}").format(_name(row.representative)),
           _("{0} {1} the representative request.").format(_name(row.representative), _("accepted") if yes else _("declined")),
           link="/representatives", kind="representative")
    return {"name": name, "status": "Active" if yes else "Ended"}


@frappe.whitelist()
def end(name, profile):
    """Either side ends the appointment."""
    row = _row(name)
    if not row:
        frappe.throw(_("Not found."))
    if profile not in (row.principal, row.representative):
        frappe.throw(_("Not yours."), frappe.PermissionError)
    _assert_profile_owner(profile)
    frappe.db.set_value("Business Representative", name, {"status": "Ended", "answered_on": now_datetime()})
    return {"name": name, "status": "Ended"}


@frappe.whitelist()
def my_appointments(profile):
    _assert_profile_owner(profile)
    rows = frappe.get_all("Business Representative", filters={"principal": profile, "status": ["!=", "Ended"]},
                          fields=["name", "representative", "status", "note", "appointed_on"], order_by="creation desc", limit_page_length=0)
    for r in rows:
        r["representative_name"] = _name(r.representative)
    return rows


@frappe.whitelist()
def my_representations(profile):
    _assert_profile_owner(profile)
    rows = frappe.get_all("Business Representative", filters={"representative": profile, "status": ["!=", "Ended"]},
                          fields=["name", "principal", "status", "note", "appointed_on"], order_by="creation desc", limit_page_length=0)
    for r in rows:
        r["principal_name"] = _name(r.principal)
        r["greeting"] = frappe.db.get_value("BusinessProfile", r.principal, "visit_greeting") or ""
    return rows


@frappe.whitelist()
def debtors_for(principal, representative):
    """The principal's debtors (from the khata), for an active representative to visit."""
    row = frappe.db.get_value("Business Representative", {"principal": principal, "representative": representative,
                                                          "status": "Active"}, "name")
    if not row:
        frappe.throw(_("You are not an active representative of this business."), frappe.PermissionError)
    _assert_profile_owner(representative)
    d = dues_summary(principal)
    return [{"party_profile": p["party_profile"], "business_name": p["business_name"], "city": p["city"],
             "due": flt(p["due"]), "last": p["last"]} for p in d["parties"] if flt(p["due"]) > 0]


def _greeting_text(principal, debtor, rep, due, visit_date, greeting):
    welcome = (greeting or "").strip() or _("Welcome. We are glad to serve you.")
    return _("{0}\n\nDear {1},\nOur representative {2} will visit you on {3}. Kindly pay the outstanding bill of {4} at the visit. Thank you.\n— {5}").format(
        welcome, debtor, rep, visit_date, "PKR {:,.0f}".format(due), principal)


@frappe.whitelist()
def send_visit_notice(principal, representative, visit_date, debtors):
    """The representative sends the notice in the principal's name to each chosen debtor. debtors: JSON list of
    {party_profile}; the amount due and the greeting are taken from the principal's own records."""
    import json
    row = frappe.db.get_value("Business Representative", {"principal": principal, "representative": representative,
                                                          "status": "Active"}, "name")
    if not row:
        frappe.throw(_("You are not an active representative of this business."), frappe.PermissionError)
    _assert_profile_owner(representative)
    if not visit_date:
        frappe.throw(_("Choose the visit date."))
    if getdate(visit_date) < getdate():
        frappe.throw(_("The visit date is in the past."))
    chosen = json.loads(debtors) if isinstance(debtors, str) else (debtors or [])
    if not chosen:
        frappe.throw(_("Choose at least one business."))
    dues = {p["party_profile"]: p for p in debtors_for_principal(principal)}
    greeting = frappe.db.get_value("BusinessProfile", principal, "visit_greeting") or ""
    sent = 0
    for c in chosen:
        p = dues.get(c.get("party_profile"))
        if not p:
            continue  # not a debtor of this business: nothing is sent
        text = _greeting_text(_name(principal), p["business_name"], _name(representative), p["due"], visit_date, greeting)
        notify(p["party_profile"], _("Visit from {0}").format(_name(principal)), text, link="/khata", kind="visit")
        frappe.get_doc({"doctype": "Visit Notice", "principal": principal, "representative": representative,
                        "debtor": p["party_profile"], "due": p["due"], "visit_date": visit_date,
                        "message": text[:1000], "sent_on": now_datetime()}).insert(ignore_permissions=True)
        sent += 1
    return {"sent": sent}


def debtors_for_principal(principal):
    return [{"party_profile": p["party_profile"], "business_name": p["business_name"], "due": flt(p["due"])}
            for p in dues_summary(principal)["parties"] if flt(p["due"]) > 0]


@frappe.whitelist()
def set_visit_greeting(profile, text=""):
    _assert_profile_owner(profile)
    frappe.db.set_value("BusinessProfile", profile, "visit_greeting", (text or "").strip()[:600])
    return {"status": "saved"}


@frappe.whitelist()
def visit_greeting(profile):
    """This business's welcome text for visit notices."""
    _assert_profile_owner(profile)
    return frappe.db.get_value("BusinessProfile", profile, "visit_greeting") or ""
