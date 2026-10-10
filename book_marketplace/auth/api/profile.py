import frappe
from frappe import _


BUYER_FIELDS = ["name", "user", "avatar", "full_name", "phone", "email", "default_address", "city", "country", "status"]
SELLER_FIELDS = ["name", "user", "profile_type", "business_name", "status", "logo", "banner_image", "business_description", "public_message", "phone", "email", "address", "city", "country"]
BANK_FIELDS = ["name", "business_profile", "bank_name", "account_title", "account_number", "iban", "branch_code", "payment_instructions", "is_default", "is_active"]
PAYMENT_FIELDS = ["name", "order", "buyer_profile", "seller_profile", "amount", "payment_method", "reference_no", "payment_date", "screenshot", "notes", "status", "creation"]


def _get_owned_business_profile(profile_id):
    doc = frappe.get_doc("BusinessProfile", profile_id)
    if doc.user != frappe.session.user and "System Manager" not in frappe.get_roles():
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return doc


def _get_or_create_buyer_profile():
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


@frappe.whitelist()
def list_bank_accounts(profile_id):
    _get_owned_business_profile(profile_id)
    return frappe.get_all("BusinessBankAccount", filters={"business_profile": profile_id}, fields=BANK_FIELDS, order_by="is_default desc, modified desc")


@frappe.whitelist()
def save_bank_account(profile_id, bank_name, account_title, account_number, account_id=None, iban=None, branch_code=None, payment_instructions=None, is_default=0, is_active=1):
    _get_owned_business_profile(profile_id)
    if account_id:
        doc = frappe.get_doc("BusinessBankAccount", account_id)
        if doc.business_profile != profile_id:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    else:
        doc = frappe.get_doc({"doctype": "BusinessBankAccount", "business_profile": profile_id})
    doc.bank_name = bank_name
    doc.account_title = account_title
    doc.account_number = account_number
    doc.iban = iban
    doc.branch_code = branch_code
    doc.payment_instructions = payment_instructions
    doc.is_default = int(is_default or 0)
    doc.is_active = int(is_active or 0)
    doc.save(ignore_permissions=True)
    if doc.is_default:
        frappe.db.set_value("BusinessBankAccount", {"business_profile": profile_id, "name": ["!=", doc.name]}, "is_default", 0)
    return {field: doc.get(field) for field in BANK_FIELDS}


@frappe.whitelist()
def create_payment_transaction(amount, payment_method, seller_profile=None, order=None, reference_no=None, payment_date=None, screenshot=None, notes=None):
    buyer = _get_or_create_buyer_profile()
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
