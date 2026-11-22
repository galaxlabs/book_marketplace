"""Basic tier: a simple party ledger (khata) per business profile.

Not accounting: no GL, no stock. Each entry moves the balance with one party.
- Credit Given / Payment Received: the party owes the business (receivable).
- Credit Taken / Payment Made: the business owes the party (payable).
"""
import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from book_marketplace.auth.api.auth import _assert_profile_owner

RECEIVABLE_UP = ("Credit Given",)
RECEIVABLE_DOWN = ("Payment Received",)
PAYABLE_UP = ("Credit Taken",)
PAYABLE_DOWN = ("Payment Made",)
ENTRY_TYPES = RECEIVABLE_UP + RECEIVABLE_DOWN + PAYABLE_UP + PAYABLE_DOWN


@frappe.whitelist()
def add_khata_entry(business_profile, party_name, entry_type, amount, entry_date=None, reference=None, notes=None, party_profile=None):
    _assert_profile_owner(business_profile)
    if entry_type not in ENTRY_TYPES:
        frappe.throw(_("Unknown entry type."))
    if flt(amount) <= 0:
        frappe.throw(_("Amount must be greater than zero."))
    party = (party_name or "").strip()
    if not party:
        frappe.throw(_("Party name is required."))
    doc = frappe.get_doc({
        "doctype": "Khata Entry",
        "business_profile": business_profile,
        "party_name": party,
        "entry_type": entry_type,
        "amount": flt(amount),
        "entry_date": getdate(entry_date) if entry_date else getdate(nowdate()),
        "reference": reference,
        "notes": notes,
    })
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "party_name": doc.party_name, "entry_type": doc.entry_type, "amount": doc.amount}


def _signed(entry_type, amount):
    """Returns (receivable_delta, payable_delta) for one entry."""
    if entry_type in RECEIVABLE_UP:
        return amount, 0
    if entry_type in RECEIVABLE_DOWN:
        return -amount, 0
    if entry_type in PAYABLE_UP:
        return 0, amount
    return 0, -amount


@frappe.whitelist()
def get_khata_summary(business_profile):
    """Balances per party plus totals. to_receive: parties owe you. to_pay: you owe parties."""
    _assert_profile_owner(business_profile)
    rows = frappe.get_all(
        "Khata Entry",
        filters={"business_profile": business_profile},
        fields=["party_name", "entry_type", "amount"],
        limit_page_length=0,
    )
    parties = {}
    for r in rows:
        p = parties.setdefault(r.party_name, {"party_name": r.party_name, "to_receive": 0.0, "to_pay": 0.0})
        recv, pay = _signed(r.entry_type, flt(r.amount))
        p["to_receive"] += recv
        p["to_pay"] += pay
    out = []
    for p in parties.values():
        p["to_receive"] = round(p["to_receive"], 2)
        p["to_pay"] = round(p["to_pay"], 2)
        p["balance"] = round(p["to_receive"] - p["to_pay"], 2)
        out.append(p)
    out.sort(key=lambda x: x["party_name"].lower())
    totals = {
        "to_receive": round(sum(p["to_receive"] for p in out), 2),
        "to_pay": round(sum(p["to_pay"] for p in out), 2),
    }
    totals["balance"] = round(totals["to_receive"] - totals["to_pay"], 2)
    return {"parties": out, "totals": totals}


@frappe.whitelist()
def list_khata_entries(business_profile, party_name=None, limit=100):
    _assert_profile_owner(business_profile)
    filters = {"business_profile": business_profile}
    if party_name:
        filters["party_name"] = party_name
    return frappe.get_all(
        "Khata Entry",
        filters=filters,
        fields=["name", "party_name", "entry_date", "entry_type", "amount", "reference", "notes"],
        order_by="entry_date desc, creation desc",
        limit_page_length=min(int(limit or 100), 500),
    )
