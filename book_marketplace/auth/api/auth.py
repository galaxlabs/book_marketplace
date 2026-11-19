import frappe
from frappe import _
from frappe.utils.password import check_password

from book_marketplace.auth.doctype.marketplace_settings.marketplace_settings import (
    enabled_profile_types,
    is_profile_type_enabled,
)

PROFILE_TYPES = ["Publisher", "Bookseller", "Distributor", "Agent"]


@frappe.whitelist(allow_guest=True)
def get_marketplace_settings():
    """Business types the app currently offers. Used by signup and the business panel."""
    return {"enabled_profile_types": enabled_profile_types()}


@frappe.whitelist(allow_guest=True)
def get_active_terms():
    terms = frappe.get_all(
        "SignupTerms",
        filters={"is_active": 1},
        fields=["name", "title", "version", "content"],
        limit=1,
    )
    return terms[0] if terms else None


@frappe.whitelist(allow_guest=True)
def get_me():
    user = frappe.session.user
    if user == "Guest":
        return {"is_authenticated": False, "user": None, "account_type": "visitor", "profiles": []}
    profiles = frappe.get_all(
        "BusinessProfile",
        filters={"user": user},
        fields=["name", "profile_type", "business_name", "status"],
    )
    buyer_profile = frappe.db.get_value("BuyerProfile", {"user": user}, "name")
    approved = [profile for profile in profiles if profile.status == "Approved"]
    pending = [profile for profile in profiles if profile.status == "Pending"]
    rejected = [profile for profile in profiles if profile.status == "Rejected"]
    if approved:
        account_type = "seller"
        home = "/dashboard"
    elif pending:
        account_type = "seller_pending"
        home = "/dashboard"
    elif rejected:
        account_type = "seller_rejected"
        home = "/dashboard"
    elif buyer_profile:
        account_type = "buyer"
        home = "/account/profile"
    else:
        account_type = "user"
        home = "/account/profile"
    return {
        "is_authenticated": True,
        "user": user,
        "account_type": account_type,
        "is_admin": _is_admin(),
        "home": home,
        "buyer_profile": buyer_profile,
        "profiles": profiles,
    }


@frappe.whitelist(allow_guest=True)
def login_with_token(usr, pwd):
    if not usr or not pwd:
        frappe.throw(_("Email and password are required."))
    user = frappe.db.get_value("User", {"email": usr}, "name") or frappe.db.get_value("User", usr, "name")
    if not user:
        frappe.throw(_("Invalid email or password."))
    check_password(user, pwd)
    if not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(_("This account is disabled. Contact the administrator."))
    user_doc = frappe.get_doc("User", user)
    if not user_doc.api_key:
        user_doc.api_key = frappe.generate_hash(length=15)
    api_secret = frappe.generate_hash(length=15)
    user_doc.api_secret = api_secret
    user_doc.save(ignore_permissions=True)
    frappe.set_user(user)
    return {"api_key": user_doc.api_key, "api_secret": api_secret, "token": f"{user_doc.api_key}:{api_secret}", "me": get_me()}


@frappe.whitelist(allow_guest=True)
def _ensure_buyer(user_name, full_name, email):
    name = frappe.db.get_value("BuyerProfile", {"user": user_name}, "name")
    if not name:
        name = frappe.get_doc({"doctype": "BuyerProfile", "user": user_name, "full_name": full_name,
                               "email": email, "status": "Active"}).insert(ignore_permissions=True).name
    return name


def _setup_new_account(user_name, full_name, email, auto_business=True):
    """Buyer profile for everyone; an approved Bookseller shop unless the signup made its own business;
    then an in-app notification that the account is ready."""
    from frappe.utils import now_datetime
    buyer = _ensure_buyer(user_name, full_name, email)
    if auto_business and not frappe.db.exists("BusinessProfile", {"user": user_name}):
        frappe.get_doc({"doctype": "BusinessProfile", "user": user_name, "profile_type": "Bookseller",
                        "business_name": full_name or email, "status": "Approved"}).insert(ignore_permissions=True)
    frappe.get_doc({"doctype": "Customer Notification", "buyer_profile": buyer, "kind": "Account",
                    "message": "آپ کا اکاؤنٹ بن گیا ہے، اور آپ کی دکان کی پروفائل تیار ہے۔ / Your account is ready and your shop profile is set up.",
                    "is_read": 0, "created_on": now_datetime()}).insert(ignore_permissions=True)


@frappe.whitelist(allow_guest=True)
def signup(email, password, full_name, business_name=None, profile_type=None, terms_accepted=0, purpose="sale"):
    if not terms_accepted:
        frappe.throw(_("You must accept the terms and conditions to sign up."))
    purpose = purpose or "sale"
    if purpose not in ["sale", "purchase"]:
        frappe.throw(_("Invalid signup purpose."))
    if frappe.db.exists("User", email):
        frappe.throw(_("User already exists."))
    if purpose == "sale" and business_name and profile_type not in PROFILE_TYPES:
        frappe.throw(_("Invalid seller type."))
    if purpose == "sale" and business_name and not profile_type:
        frappe.throw(_("Business name and profile type are required."))
    if purpose == "sale" and business_name and not is_profile_type_enabled(profile_type):
        frappe.throw(_("This business type is not enabled in Marketplace Settings."))

    # Validate everything before creating the user, so a failed signup never leaves an orphan User.
    roles = [{"role": "Website User"}]
    if purpose == "sale":
        ensure_role("Seller Applicant")
        roles.append({"role": "Seller Applicant"})
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": full_name,
        "new_password": password,
        "roles": roles,
    })
    user.insert(ignore_permissions=True)

    if purpose == "purchase":
        ensure_role("Customer")
        user.add_roles("Customer")
        buyer = frappe.get_doc({
            "doctype": "BuyerProfile",
            "user": user.name,
            "full_name": full_name,
            "email": email,
            "status": "Active",
        })
        buyer.insert(ignore_permissions=True)
        _setup_new_account(user.name, full_name, email, auto_business=False)
        return {"user": user.name, "profile": None, "status": "Active", "purpose": "purchase"}

    if not business_name or not profile_type:
        _setup_new_account(user.name, full_name, email, auto_business=True)
    return {"user": user.name, "profile": None, "status": "Setup Required", "purpose": "sale"}

    profile = frappe.get_doc({
        "doctype": "BusinessProfile",
        "user": user.name,
        "profile_type": profile_type,
        "business_name": business_name,
        "status": "Pending",
    })
    profile.insert(ignore_permissions=True)
    active_terms = frappe.get_all("SignupTerms", filters={"is_active": 1}, fields=["name"], limit=1)
    if active_terms:
        frappe.db.set_value("BusinessProfile", profile.name, "accepted_terms", active_terms[0].name)
    _setup_new_account(user.name, full_name, email, auto_business=False)
    return {"user": user.name, "profile": profile.name, "status": "Pending", "purpose": "sale"}


def ensure_role(role_name):
    if not frappe.db.exists("Role", role_name):
        frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


@frappe.whitelist()
def create_business_profile(business_name, profile_type, terms_accepted=0):
    if frappe.session.user == "Guest":
        frappe.throw(_("Login is required to create a business profile."), frappe.PermissionError)
    if not terms_accepted:
        frappe.throw(_("You must accept the terms and conditions."))
    # A user may hold one profile per business type (publisher, bookseller, distributor).
    if frappe.db.exists("BusinessProfile", {"user": frappe.session.user, "profile_type": profile_type}):
        frappe.throw(_("You already have a {0} profile.").format(profile_type))
    if profile_type not in PROFILE_TYPES:
        frappe.throw(_("Invalid seller type."))
    if not is_profile_type_enabled(profile_type):
        frappe.throw(_("This business type is not enabled in Marketplace Settings."))
    profile = frappe.get_doc({
        "doctype": "BusinessProfile",
        "user": frappe.session.user,
        "profile_type": profile_type,
        "business_name": business_name,
        "status": "Pending",
    })
    profile.insert(ignore_permissions=True)
    active_terms = frappe.get_all("SignupTerms", filters={"is_active": 1}, fields=["name"], limit=1)
    if active_terms:
        frappe.db.set_value("BusinessProfile", profile.name, "accepted_terms", active_terms[0].name)
    return {"name": profile.name, "status": profile.status, "profile_type": profile.profile_type, "business_name": profile.business_name}


@frappe.whitelist()
def list_bank_accounts(profile_id):
    _assert_profile_owner(profile_id)
    return frappe.get_all(
        "BusinessBankAccount",
        filters={"business_profile": profile_id},
        fields=[
            "name", "business_profile", "bank_name", "account_title", "account_number",
            "iban", "branch_code", "payment_instructions", "is_default", "is_active"
        ],
        order_by="is_default desc, modified desc",
    )


@frappe.whitelist()
def add_bank_account(
    business_profile,
    bank_name,
    account_title,
    account_number,
    iban=None,
    branch_code=None,
    payment_instructions=None,
    is_default=0,
):
    _assert_profile_owner(business_profile)
    doc = frappe.get_doc({
        "doctype": "BusinessBankAccount",
        "business_profile": business_profile,
        "bank_name": bank_name,
        "account_title": account_title,
        "account_number": account_number,
        "iban": iban,
        "branch_code": branch_code,
        "payment_instructions": payment_instructions,
        "is_default": is_default,
        "is_active": 1,
    })
    doc.insert(ignore_permissions=True)
    if doc.is_default:
        _clear_other_default_bank_accounts(doc.business_profile, doc.name)
    return doc.as_dict()


@frappe.whitelist()
def set_default_bank_account(account_id):
    doc = frappe.get_doc("BusinessBankAccount", account_id)
    _assert_profile_owner(doc.business_profile)
    _clear_other_default_bank_accounts(doc.business_profile, doc.name)
    doc.is_default = 1
    doc.is_active = 1
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "is_default": doc.is_default}


@frappe.whitelist()
def toggle_bank_account(account_id, is_active):
    doc = frappe.get_doc("BusinessBankAccount", account_id)
    _assert_profile_owner(doc.business_profile)
    doc.is_active = 1 if int(is_active) else 0
    if not doc.is_active:
        doc.is_default = 0
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "is_active": doc.is_active}


def _clear_other_default_bank_accounts(profile_id, keep_account=None):
    filters = {"business_profile": profile_id, "is_default": 1}
    if keep_account:
        filters["name"] = ["!=", keep_account]
    for account in frappe.get_all("BusinessBankAccount", filters=filters, pluck="name"):
        frappe.db.set_value("BusinessBankAccount", account, "is_default", 0)


def _is_admin():
    return "System Manager" in frappe.get_roles()


def _assert_admin():
    if not _is_admin():
        frappe.throw(_("Only an administrator can do this."), frappe.PermissionError)


def _assert_profile_owner(profile_id):
    if _is_admin():
        return
    owner = frappe.db.get_value("BusinessProfile", profile_id, "user")
    if owner != frappe.session.user:
        frappe.throw(_("Not permitted for this business profile."), frappe.PermissionError)


@frappe.whitelist()
def get_my_profiles(user=None):
    # Users see only their own profiles. Admins may pass another user to inspect it.
    if user and user != frappe.session.user:
        _assert_admin()
    target = user or frappe.session.user
    return frappe.get_all(
        "BusinessProfile",
        filters={"user": target},
        fields=["name", "profile_type", "business_name", "status", "logo", "banner_image", "business_description", "public_message", "phone", "email", "address", "city", "country"],
    )


@frappe.whitelist()
def approve_profile(profile_id):
    _assert_admin()
    doc = frappe.get_doc("BusinessProfile", profile_id)
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_on = frappe.utils.today()
    doc.save()
    user = frappe.get_doc("User", doc.user)
    user.add_roles(doc.profile_type)
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def reject_profile(profile_id):
    _assert_admin()
    doc = frappe.get_doc("BusinessProfile", profile_id)
    doc.status = "Rejected"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_pending_profiles():
    _assert_admin()
    return frappe.get_all(
        "BusinessProfile",
        filters={"status": "Pending"},
        fields=["name", "business_name", "profile_type", "user", "creation"],
    )
