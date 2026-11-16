import frappe
from frappe import _
from frappe.utils import flt


BUYER_FIELDS = ["name", "user", "avatar", "full_name", "phone", "email", "default_address", "city", "country", "status"]
SELLER_FIELDS = ["name", "user", "profile_type", "business_name", "status", "logo", "banner_image", "business_description", "public_message", "phone", "email", "address", "city", "country"]
PAYMENT_FIELDS = ["name", "order", "buyer_profile", "seller_profile", "amount", "payment_method", "reference_no", "payment_date", "screenshot", "notes", "status", "creation"]


def _get_owned_business_profile(profile_id):
    doc = frappe.get_doc("BusinessProfile", profile_id)
    if doc.user != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return doc


def _get_or_create_buyer_profile():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login is required."), frappe.PermissionError)
    existing = frappe.db.get_value("BuyerProfile", {"user": frappe.session.user}, "name")
    if existing:
        return frappe.get_doc("BuyerProfile", existing)
    user = frappe.get_doc("User", frappe.session.user)
    doc = frappe.get_doc({
        "doctype": "BuyerProfile",
        "user": frappe.session.user,
        "full_name": user.full_name or user.first_name or frappe.session.user,
        "email": user.email,
        "status": "Active",
    })
    doc.insert(ignore_permissions=True)
    return doc


@frappe.whitelist()
def get_my_buyer_profile():
    doc = _get_or_create_buyer_profile()
    return {field: doc.get(field) for field in BUYER_FIELDS}


@frappe.whitelist()
def update_buyer_profile(full_name=None, phone=None, email=None, default_address=None, city=None, country=None, avatar=None):
    doc = _get_or_create_buyer_profile()
    for field, value in {
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "default_address": default_address,
        "city": city,
        "country": country,
        "avatar": avatar,
    }.items():
        if value is not None:
            setattr(doc, field, value)
    doc.save(ignore_permissions=True)
    return {field: doc.get(field) for field in BUYER_FIELDS}


@frappe.whitelist()
def update_business_profile(profile_id, business_name=None, logo=None, banner_image=None, business_description=None, public_message=None, phone=None, email=None, address=None, city=None, country=None):
    doc = _get_owned_business_profile(profile_id)
    for field, value in {
        "business_name": business_name,
        "logo": logo,
        "banner_image": banner_image,
        "business_description": business_description,
        "public_message": public_message,
        "phone": phone,
        "email": email,
        "address": address,
        "city": city,
        "country": country,
    }.items():
        if value is not None:
            setattr(doc, field, value)
    doc.save(ignore_permissions=True)
    return {field: doc.get(field) for field in SELLER_FIELDS}


# Bank accounts are managed in auth/api/auth.py (list_bank_accounts, add_bank_account, ...).


@frappe.whitelist()
def create_payment_transaction(amount, payment_method, seller_profile=None, order=None, reference_no=None, payment_date=None, screenshot=None, notes=None):
    buyer = _get_or_create_buyer_profile()
    if not flt(amount) > 0:
        frappe.throw(_("Payment amount must be greater than zero."))
    if seller_profile and not frappe.db.exists("BusinessProfile", seller_profile):
        frappe.throw(_("Seller profile not found."))
    doc = frappe.get_doc({
        "doctype": "PaymentTransaction",
        "order": order,
        "buyer_profile": buyer.name,
        "seller_profile": seller_profile,
        "amount": amount,
        "payment_method": payment_method,
        "reference_no": reference_no,
        "payment_date": payment_date or frappe.utils.today(),
        "screenshot": screenshot,
        "notes": notes,
        "status": "Pending",
    })
    doc.insert(ignore_permissions=True)
    return {field: doc.get(field) for field in PAYMENT_FIELDS}


@frappe.whitelist()
def list_my_payments():
    buyer = _get_or_create_buyer_profile()
    return frappe.get_all("PaymentTransaction", filters={"buyer_profile": buyer.name}, fields=PAYMENT_FIELDS, order_by="creation desc")
