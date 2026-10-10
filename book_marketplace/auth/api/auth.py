import frappe
from frappe import _
from frappe.utils.password import check_password


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
    buyer_profile = frappe.db.get_value("BuyerProfile", {"user": user}, "name") if frappe.db.exists("DocType", "BuyerProfile") else None
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
    user_doc = frappe.get_doc("User", user)
    if not user_doc.api_key:
        user_doc.api_key = frappe.generate_hash(length=15)
    api_secret = frappe.generate_hash(length=15)
    user_doc.api_secret = api_secret
    user_doc.save(ignore_permissions=True)
    frappe.set_user(user)
    return {"api_key": user_doc.api_key, "api_secret": api_secret, "token": f"{user_doc.api_key}:{api_secret}", "me": get_me()}


@frappe.whitelist(allow_guest=True)
def signup(email, password, full_name, business_name=None, profile_type=None, terms_accepted=0, purpose="sale"):
    if not terms_accepted:
        frappe.throw(_("You must accept the terms and conditions to sign up."))
    if frappe.db.exists("User", email):
        frappe.throw(_("User already exists."))
    purpose = purpose or "sale"
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

    if purpose == "sale" and not business_name:
        return {"user": user.name, "profile": None, "status": "Setup Required", "purpose": "sale"}

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
        return {"user": user.name, "profile": None, "status": "Active", "purpose": "purchase"}

    if not business_name or not profile_type:
        frappe.throw(_("Business name and profile type are required."))

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
    return {"user": user.name, "profile": profile.name, "status": "Pending", "purpose": "sale"}


def ensure_role(role_name):
    if not frappe.db.exists("Role", role_name):
        frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


@frappe.whitelist()
def create_business_profile(business_name, profile_type, terms_accepted=0):
    if not terms_accepted:
        frappe.throw(_("You must accept the terms and conditions."))
    if frappe.db.exists("BusinessProfile", {"user": frappe.session.user}):
        frappe.throw(_("Business profile already exists for this user."))
    if profile_type not in ["Publisher", "Bookseller", "Distributor", "Agent"]:
        frappe.throw(_("Invalid seller type."))
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
def get_my_profiles(user=None):
    if not user:
        user = frappe.session.user
    profiles = frappe.get_all(
        "BusinessProfile",
        filters={"user": user},
        fields=["name", "profile_type", "business_name", "status", "logo", "banner_image", "business_description", "public_message", "phone", "email", "address", "city", "country"],
    )
    return profiles


@frappe.whitelist()
def approve_profile(profile_id):
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
    doc = frappe.get_doc("BusinessProfile", profile_id)
    doc.status = "Rejected"
    doc.save()
    return {"name": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True)
def get_pending_profiles():
    profiles = frappe.get_all(
        "BusinessProfile",
        filters={"status": "Pending"},
        fields=["name", "business_name", "profile_type", "user", "creation"],
    )
    return profiles
