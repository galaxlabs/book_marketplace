"""Customer orders, step 4: privacy. Row rules for list views (permission_query_conditions) and single-document checks
(has_permission). A buyer sees only own baskets and orders; a shop sees only orders where it is the seller."""
import frappe

PRIVILEGED = {"Administrator", "System Manager"}


def _buyer_profiles(user):
    return frappe.get_all("BuyerProfile", filters={"user": user}, pluck="name")


def _seller_profiles(user):
    return frappe.get_all("BusinessProfile", filters={"user": user}, pluck="name")


def _sql_in(values):
    return ", ".join(frappe.db.escape(v) for v in values)


def order_query(user=None):
    user = user or frappe.session.user
    if user in PRIVILEGED:
        return ""
    buyers = _buyer_profiles(user)
    sellers = _seller_profiles(user)
    parts = []
    if buyers:
        parts.append("`tabCustomer Order`.buyer_profile in (%s)" % _sql_in(buyers))
    if sellers:
        parts.append("`tabCustomer Order`.seller_profile in (%s)" % _sql_in(sellers))
    return "(" + " or ".join(parts) + ")" if parts else "1=0"


def basket_query(user=None):
    user = user or frappe.session.user
    if user in PRIVILEGED:
        return ""
    buyers = _buyer_profiles(user)
    return "`tabCustomer Basket`.buyer_profile in (%s)" % _sql_in(buyers) if buyers else "1=0"


def order_has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    if user in PRIVILEGED:
        return True
    if doc.buyer_profile in _buyer_profiles(user):
        return True
    return doc.seller_profile in _seller_profiles(user)


def basket_has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    if user in PRIVILEGED:
        return True
    return doc.buyer_profile in _buyer_profiles(user)
