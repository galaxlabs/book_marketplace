"""Transport companies, city preferences, and the freight policy.

- Anyone with a business profile (shop, publisher, buyer) can add a transport company and list the cities it serves.
- A shop lists its preferred companies per city (ranked). A publisher lists the companies it uses per city.
  A shop's list is visible to every signed-in account, so a publisher can see it.
- Freight policy: rules by scenario, city, number of copies and order total. The best rule wins:
  higher priority first, then a rule for the city over a rule for any city, then a narrower copy range.
  The charge is fixed, per copy, or a percent of the order total. Who pays: buyer, seller, or split.
"""
import frappe
from frappe import _
from frappe.utils import flt

from book_marketplace.auth.api.auth import _assert_profile_owner

SCENARIOS = (
    "Publisher office to shop door",
    "Publisher office to transport to shop city",
    "Publisher office to publisher city transport office",
)


def _me():
    if not frappe.session.user or frappe.session.user == "Guest":
        frappe.throw(_("Please log in."), frappe.AuthenticationError)
    return frappe.session.user


def _norm_city(city):
    return " ".join((city or "").split()).strip()


# ---------- transport companies ----------

@frappe.whitelist()
def add_transport_company(profile_id, name, cities, phone="", notes=""):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "settings.manage")
    name = " ".join((name or "").split()).strip()
    if not name:
        frappe.throw(_("Write the company name."))
    city_list = [c.strip() for c in (cities or "").replace("،", ",").split(",") if c.strip()]
    if not city_list:
        frappe.throw(_("Write at least one city the company serves."))
    existing = frappe.db.get_value("Transport Company", {"name_of_company": name}, "name")
    if existing:
        return {"name": existing, "status": "exists"}
    doc = frappe.get_doc({"doctype": "Transport Company", "name_of_company": name,
                          "cities": ", ".join(city_list), "phone": (phone or "").strip()[:40],
                          "notes": (notes or "").strip()[:300], "added_by": profile_id, "status": "Active"}
                         ).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "added"}


@frappe.whitelist()
def list_transport_companies(city=""):
    _me()
    rows = frappe.get_all("Transport Company", filters={"status": "Active"},
                          fields=["name", "name_of_company", "cities", "phone", "notes"],
                          order_by="name_of_company asc", limit_page_length=0)
    c = _norm_city(city).lower()
    if c:
        rows = [r for r in rows if c in (r.cities or "").lower()]
    return rows


# ---------- city preferences ----------

@frappe.whitelist()
def set_city_preference(profile_id, city, transporter, purpose, rank=1):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "settings.manage")
    city = _norm_city(city)
    if not city:
        frappe.throw(_("Write the city."))
    if purpose not in ("Shop preferred", "Publisher uses"):
        frappe.throw(_("Purpose must be Shop preferred or Publisher uses."))
    if not frappe.db.exists("Transport Company", transporter):
        frappe.throw(_("Transport company not found."))
    key = {"business_profile": profile_id, "city": city, "transporter": transporter, "purpose": purpose}
    existing = frappe.db.get_value("Transport Preference", key, "name")
    if existing:
        frappe.db.set_value("Transport Preference", existing, "rank", frappe.utils.cint(rank) or 1)
        return {"name": existing, "status": "updated"}
    doc = frappe.get_doc({"doctype": "Transport Preference", **key, "rank": frappe.utils.cint(rank) or 1}).insert(ignore_permissions=True)
    return {"name": doc.name, "status": "added"}


@frappe.whitelist()
def remove_city_preference(name, profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "settings.manage")
    if frappe.db.get_value("Transport Preference", name, "business_profile") != profile_id:
        frappe.throw(_("This preference is not yours."), frappe.PermissionError)
    frappe.delete_doc("Transport Preference", name, ignore_permissions=True)
    return {"status": "removed"}


def _grouped(profile_id, purpose):
    rows = frappe.get_all("Transport Preference",
                          filters={"business_profile": profile_id, "purpose": purpose},
                          fields=["name", "city", "transporter", "rank"], order_by="city asc, rank asc",
                          limit_page_length=0)
    names = {c.name: c.name_of_company for c in frappe.get_all("Transport Company", filters={"name": ["in", [r.transporter for r in rows] or [""]]},
                                                               fields=["name", "name_of_company"])}
    out = {}
    for r in rows:
        out.setdefault(r.city, []).append({"name": r.name, "transporter": r.transporter,
                                          "company": names.get(r.transporter, r.transporter), "rank": r.rank})
    return [{"city": c, "companies": items} for c, items in sorted(out.items())]


@frappe.whitelist()
def my_city_preferences(profile_id):
    from book_marketplace.auth.api.auth import _assert_profile_access
    _assert_profile_access(profile_id, "orders.view")
    return {"shop_preferred": _grouped(profile_id, "Shop preferred"),
            "publisher_uses": _grouped(profile_id, "Publisher uses")}


@frappe.whitelist()
def shop_city_preferences(shop_profile):
    """What a shop prefers, city by city. Any signed-in account can read it (a publisher uses it to choose)."""
    _me()
    return _grouped(shop_profile, "Shop preferred")


# ---------- freight policy ----------

def _rule_matches(rule, scenario, city, copies, total):
    if rule.scenario not in ("Any", scenario):
        return False
    if rule.city and _norm_city(rule.city).lower() != _norm_city(city).lower():
        return False
    if frappe.utils.cint(rule.min_copies) and copies < frappe.utils.cint(rule.min_copies):
        return False
    if frappe.utils.cint(rule.max_copies) and copies > frappe.utils.cint(rule.max_copies):
        return False
    if flt(rule.min_total) and total < flt(rule.min_total):
        return False
    return True


def quote_freight(scenario, city, copies, total):
    """The freight for one order under the active rules. Returns the rule used and the shares."""
    copies = int(copies or 0)
    total = flt(total)
    rules = frappe.get_all("Freight Rule", filters={"active": 1},
                           fields=["name", "rule_name", "scenario", "city", "min_copies", "max_copies", "min_total",
                                   "priority", "charge_type", "amount", "payer", "seller_share"],
                           limit_page_length=0)
    matching = [r for r in rules if _rule_matches(r, scenario, city, copies, total)]
    if not matching:
        return {"rule": None, "total": 0.0, "buyer_share": 0.0, "seller_share": 0.0, "payer": "Buyer"}
    matching.sort(key=lambda r: (-flt(r.priority), 0 if r.city else 1, (frappe.utils.cint(r.max_copies) == 0)))
    r = matching[0]
    if r.charge_type == "Per Copy":
        amount = flt(r.amount) * copies
    elif r.charge_type == "Percent of Total":
        amount = total * flt(r.amount) / 100
    else:
        amount = flt(r.amount)
    amount = round(amount, 2)
    if r.payer == "Seller":
        seller, buyer = amount, 0.0
    elif r.payer == "Split":
        seller = round(amount * flt(r.seller_share) / 100, 2)
        buyer = round(amount - seller, 2)
    else:
        seller, buyer = 0.0, amount
    return {"rule": r.rule_name, "total": amount, "buyer_share": buyer, "seller_share": seller, "payer": r.payer}


@frappe.whitelist()
def preview_freight(scenario, city, copies, total):
    _me()
    return quote_freight(scenario, city, copies, total)


@frappe.whitelist()
def create_freight_rule(rule_name, scenario, charge_type, payer, amount, city="", min_copies=0, max_copies=0,
                        min_total=0, priority=0, seller_share=0):
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only an admin sets freight rules."), frappe.PermissionError)
    doc = frappe.get_doc({"doctype": "Freight Rule", "rule_name": rule_name, "scenario": scenario,
                          "charge_type": charge_type, "payer": payer, "amount": flt(amount), "city": _norm_city(city),
                          "min_copies": int(min_copies or 0), "max_copies": int(max_copies or 0),
                          "min_total": flt(min_total), "priority": int(priority or 0),
                          "seller_share": flt(seller_share), "active": 1}).insert(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def list_freight_rules():
    _me()
    return frappe.get_all("Freight Rule", fields=["name", "rule_name", "scenario", "city", "charge_type", "amount",
                                                  "payer", "priority", "active"],
                          order_by="priority desc", limit_page_length=0)
