import frappe
from frappe import _


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
def signup(email, password, full_name, business_name, profile_type, terms_accepted=0):
    if not terms_accepted:
        frappe.throw(_("You must accept the terms and conditions to sign up."))
    if frappe.db.exists("User", email):
        frappe.throw(_("User already exists."))
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": full_name,
        "new_password": password,
        "roles": [{"role": profile_type}],
    })
    user.insert(ignore_permissions=True)
    profile = frappe.get_doc({
        "doctype": "BusinessProfile",
        "user": user.name,
        "profile_type": profile_type,
        "business_name": business_name,
        "status": "Pending",
    })
    profile.insert(ignore_permissions=True)
    active_terms = frappe.get_all("SignupTerms", filters={"is_active": 1}, limit=1)
    if active_terms:
        profile.db_set("custom_terms_accepted", active_terms[0].name)
    return {"user": user.name, "profile": profile.name, "status": "Pending"}


@frappe.whitelist()
def get_my_profiles(user=None):
    if not user:
        user = frappe.session.user
    profiles = frappe.get_all(
        "BusinessProfile",
        filters={"user": user},
        fields=["name", "profile_type", "business_name", "status"],
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
