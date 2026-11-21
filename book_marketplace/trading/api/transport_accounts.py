"""Transport companies' offices and accounts for transport companies, couriers and porters.

- Offices: anyone adds an office (city, address, phone) to a transport company. One shared record for everyone.
- Accounts: any signed-in user asks for an account for a company (as the company, a courier, or a porter).
  A System Manager approves or rejects it. An approved account sees the company's loads: dispatched orders
  that went with that company.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

KINDS = ("Transport Company", "Courier", "Porter")


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


def _is_admin():
    return "System Manager" in frappe.get_roles()


@frappe.whitelist()
def company_detail(company):
    _me()
    if not frappe.db.exists("Transport Company", company):
        frappe.throw(_("Transport company not found."))
    doc = frappe.get_doc("Transport Company", company)
    return {
        "name": doc.name, "company": doc.name_of_company, "cities": doc.cities, "phone": doc.phone or "",
        "notes": doc.notes or "",
        "offices": [{"city": o.city, "address": o.address, "phone": o.phone or "", "contact_person": o.contact_person or ""}
                    for o in doc.offices],
    }


@frappe.whitelist()
def add_office(company, city, address, phone="", contact_person=""):
    _me()
    city = " ".join((city or "").split())
    address = (address or "").strip()
    if not city or not address:
        frappe.throw(_("Write the city and the address."))
    doc = frappe.get_doc("Transport Company", company)
    doc.append("offices", {"city": city[:140], "address": address[:300], "phone": (phone or "").strip()[:40],
                           "contact_person": (contact_person or "").strip()[:140]})
    doc.save(ignore_permissions=True)
    return {"ok": True, "offices": len(doc.offices)}


@frappe.whitelist()
def request_transport_account(company, kind, full_name="", phone=""):
    user = _me()
    if kind not in KINDS:
        frappe.throw(_("Kind must be Transport Company, Courier or Porter."))
    if not frappe.db.exists("Transport Company", company):
        frappe.throw(_("Transport company not found."))
    existing = frappe.db.get_value("Transport Account", {"user": user, "company": company}, "name")
    if existing:
        return {"name": existing, "status": frappe.db.get_value("Transport Account", existing, "status")}
    doc = frappe.get_doc({"doctype": "Transport Account", "user": user, "company": company, "kind": kind,
                          "full_name": (full_name or "").strip()[:140], "phone": (phone or "").strip()[:40],
                          "status": "Pending"}).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "Pending"}


@frappe.whitelist()
def my_transport_accounts():
    user = _me()
    rows = frappe.get_all("Transport Account", filters={"user": user},
                          fields=["name", "company", "kind", "status", "full_name"], limit_page_length=0)
    for r in rows:
        r["company_name"] = frappe.db.get_value("Transport Company", r.company, "name_of_company") or r.company
    return rows


@frappe.whitelist()
def decide_transport_account(name, decision):
    if not _is_admin():
        frappe.throw(_("Only an admin decides transport accounts."), frappe.PermissionError)
    if decision not in ("Approved", "Rejected"):
        frappe.throw(_("Decision must be Approved or Rejected."))
    doc = frappe.get_doc("Transport Account", name)
    doc.status = decision
    doc.decided_by = frappe.session.user
    doc.decided_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def pending_transport_accounts():
    if not _is_admin():
        frappe.throw(_("Only an admin sees the requests."), frappe.PermissionError)
    return frappe.get_all("Transport Account", filters={"status": "Pending"},
                          fields=["name", "company", "kind", "full_name", "phone", "user"], limit_page_length=0)


@frappe.whitelist()
def loads():
    """Orders dispatched with the company of the signed-in approved account (company, courier or porter)."""
    user = _me()
    accounts = frappe.get_all("Transport Account", filters={"user": user, "status": "Approved"},
                              pluck="company", limit_page_length=0)
    if not accounts:
        return []
    rows = frappe.get_all("Purchase Order Request",
                          filters={"transport_company": ["in", accounts], "dispatched_on": ["is", "set"]},
                          fields=["name", "status", "buyer_profile", "seller_profile", "bilty_no", "rider_name",
                                  "rider_phone", "total_qty", "dispatched_on", "transport_company"],
                          order_by="dispatched_on desc", limit_page_length=200)
    names = {p.name: p.business_name for p in frappe.get_all("BusinessProfile",
             filters={"name": ["in", list({r.buyer_profile for r in rows} | {r.seller_profile for r in rows} or [""])]},
             fields=["name", "business_name", "city"])}
    cities = {p.name: p.city for p in frappe.get_all("BusinessProfile",
              filters={"name": ["in", list({r.buyer_profile for r in rows} | {r.seller_profile for r in rows} or [""])]},
              fields=["name", "city"])}
    for r in rows:
        r["seller_name"] = names.get(r.seller_profile, r.seller_profile)
        r["buyer_name"] = names.get(r.buyer_profile, r.buyer_profile)
        r["from_city"] = cities.get(r.seller_profile) or ""
        r["to_city"] = cities.get(r.buyer_profile) or ""
        r["dispatched_on"] = str(r.dispatched_on or "")
    return rows
