"""Links between shops and publishers, from the catalogs alone (nothing new is stored).

- shop_publishers: the publishers whose books this shop has in its catalog (its publisher list).
- publisher_stockists: the shops that have at least one of this publisher's books in their catalog.
Only names, cities and counts are returned; no contact details."""
import frappe
from frappe import _

from book_marketplace.auth.api.auth import _assert_profile_owner


def _profiles(names):
    if not names:
        return {}
    return {p.name: p for p in frappe.get_all("BusinessProfile", filters={"name": ["in", list(names)]},
                                               fields=["name", "business_name", "city", "profile_type"])}


@frappe.whitelist()
def shop_publishers(profile_id):
    _assert_profile_owner(profile_id)
    rows = frappe.db.sql("""
        SELECT b.publisher AS publisher, COUNT(*) AS books
        FROM `tabBusinessCatalogItem` bci
        INNER JOIN `tabBook` b ON b.name = bci.book
        WHERE bci.business_profile = %s AND bci.is_active = 1 AND IFNULL(b.publisher, '') != ''
        GROUP BY b.publisher
        ORDER BY books DESC
    """, (profile_id,), as_dict=True)
    info = _profiles([r.publisher for r in rows])
    return [{"publisher": r.publisher, "business_name": (info.get(r.publisher) or frappe._dict()).get("business_name") or r.publisher,
             "city": (info.get(r.publisher) or frappe._dict()).get("city") or "", "books": r.books} for r in rows]


@frappe.whitelist()
def publisher_stockists(profile_id):
    _assert_profile_owner(profile_id)
    if frappe.db.get_value("BusinessProfile", profile_id, "profile_type") != "Publisher":
        frappe.throw(_("Only a publisher sees its stockists."), frappe.PermissionError)
    rows = frappe.db.sql("""
        SELECT bci.business_profile AS shop, COUNT(*) AS books
        FROM `tabBusinessCatalogItem` bci
        INNER JOIN `tabBook` b ON b.name = bci.book
        WHERE b.publisher = %s AND bci.business_profile != %s AND bci.is_active = 1
        GROUP BY bci.business_profile
        ORDER BY books DESC
    """, (profile_id, profile_id), as_dict=True)
    info = _profiles([r.shop for r in rows])
    return [{"shop": r.shop, "business_name": (info.get(r.shop) or frappe._dict()).get("business_name") or r.shop,
             "city": (info.get(r.shop) or frappe._dict()).get("city") or "", "books": r.books} for r in rows]
