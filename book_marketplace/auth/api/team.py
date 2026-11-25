"""A business's team: the owner invites people, gives each a role, and can add or take away single permissions."""
import secrets

import frappe
from frappe.rate_limiter import rate_limit
from frappe import _
from frappe.utils import now_datetime, validate_email_address

from book_marketplace.auth.permissions import PERMISSIONS, ROLES, assert_access, codes, has_access, member_permissions, permissions_of


def _member_row(profile_id, member):
    row = frappe.db.get_value("BusinessMember", member, ["name", "business_profile"], as_dict=True)
    if not row or row.business_profile != profile_id:
        frappe.throw(_("This member is not in your team."), frappe.PermissionError)
    return frappe.get_doc("BusinessMember", member)


def _clean_codes(value):
    text = (",".join(value) if isinstance(value, (list, tuple)) else (value or "")).replace(",", " ").split()
    bad = [c for c in text if c not in PERMISSIONS]
    if bad:
        frappe.throw(_("Unknown permission: {0}").format(", ".join(bad)))
    return ",".join(sorted(set(text)))


@frappe.whitelist()
def permission_catalog():
    """Every permission and every role with what it gives. For the team page."""
    return {
        "permissions": [{"code": c, "ur": v[0], "en": v[1]} for c, v in PERMISSIONS.items()],
        "roles": [{"role": r, "ur": v[0], "en": v[1], "permissions": sorted(v[2])} for r, v in ROLES.items()],
    }


@frappe.whitelist()
def my_permissions(profile_id):
    """What the signed-in user may do in this business (["*"] for the owner)."""
    perms = permissions_of(profile_id)
    owner = frappe.db.get_value("BusinessProfile", profile_id, "user") == frappe.session.user
    role = "Owner" if owner else (frappe.db.get_value("BusinessMember", {"business_profile": profile_id, "user": frappe.session.user, "is_active": 1}, "role") or "")
    return {"role": role, "permissions": sorted(perms)}


@frappe.whitelist()
def list_members(profile_id):
    assert_access(profile_id, "team.manage")
    owner = frappe.db.get_value("BusinessProfile", profile_id, "user")
    rows = frappe.get_all("BusinessMember", filters={"business_profile": profile_id},
                          fields=["name", "user", "email", "full_name", "role", "status", "is_active", "extra_permissions",
                                  "removed_permissions", "invited_on", "accepted_on", "invite_token"],
                          order_by="creation asc", limit_page_length=0)
    for r in rows:
        r["permissions"] = sorted(member_permissions(r.role, r.extra_permissions, r.removed_permissions))
        r["invite_path"] = f"/join?token={r.invite_token}" if r.status == "Invited" and r.invite_token else ""
        r.pop("invite_token", None)
        r["invited_on"] = str(r["invited_on"] or "")
        r["accepted_on"] = str(r["accepted_on"] or "")
    return {"owner": owner, "members": rows}


@frappe.whitelist()
def add_member(profile_id, email, role, full_name=None):
    """Invite a person. Nothing is sent: the owner gets a link to give the person. The person sets their own password."""
    assert_access(profile_id, "team.manage")
    email = (email or "").strip().lower()
    if not validate_email_address(email):
        frappe.throw(_("Write a correct email address."))
    if role not in ROLES:
        frappe.throw(_("Choose a role: {0}").format(", ".join(ROLES)))
    doc = frappe.get_doc({
        "doctype": "BusinessMember", "business_profile": profile_id, "email": email, "role": role,
        "full_name": (full_name or "").strip()[:80], "status": "Invited", "is_active": 1,
        "invite_token": secrets.token_urlsafe(32), "invited_by": frappe.session.user, "invited_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    return {"member": doc.name, "invite_path": f"/join?token={doc.invite_token}"}


@frappe.whitelist()
def set_member(profile_id, member, role=None, is_active=None, extra_permissions=None, removed_permissions=None):
    """Change a member's role, switch the person on or off, or add or take away single permissions."""
    assert_access(profile_id, "team.manage")
    doc = _member_row(profile_id, member)
    if role is not None:
        if role not in ROLES:
            frappe.throw(_("Choose a role: {0}").format(", ".join(ROLES)))
        doc.role = role
    if is_active is not None:
        on = 1 if str(is_active) in ("1", "true", "True") else 0
        doc.is_active = on
        if doc.status in ("Active", "Disabled"):
            doc.status = "Active" if on else "Disabled"
    if extra_permissions is not None:
        doc.extra_permissions = _clean_codes(extra_permissions)
    if removed_permissions is not None:
        doc.removed_permissions = _clean_codes(removed_permissions)
    doc.save(ignore_permissions=True)
    return {"member": doc.name, "role": doc.role, "is_active": doc.is_active,
            "permissions": sorted(member_permissions(doc.role, doc.extra_permissions, doc.removed_permissions))}


@frappe.whitelist()
def remove_member(profile_id, member):
    assert_access(profile_id, "team.manage")
    doc = _member_row(profile_id, member)
    frappe.delete_doc("BusinessMember", doc.name, ignore_permissions=True)
    return {"removed": member}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=600)
def invite_info(token):
    """What the join page shows to the person who holds the link: the business, the role, the email."""
    row = frappe.db.get_value("BusinessMember", {"invite_token": token or "", "status": "Invited"},
                              ["business_profile", "role", "email", "full_name"], as_dict=True) if token else None
    if not row:
        frappe.throw(_("This invitation is not valid or was already used."), frappe.DoesNotExistError)
    return {"business_name": frappe.db.get_value("BusinessProfile", row.business_profile, "business_name") or "",
            "role": row.role, "role_ur": ROLES[row.role][0], "email": row.email, "full_name": row.full_name or "",
            "user_exists": bool(frappe.db.exists("User", row.email))}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=600)
def accept_invite(token, full_name=None, password=None):
    """Join the team. A new person sets a name and a password. Someone who already has an account must be signed in with
    the invited email. The invitation works once."""
    row = frappe.db.get_value("BusinessMember", {"invite_token": token or "", "status": "Invited"}, "name") if token else None
    if not row:
        frappe.throw(_("This invitation is not valid or was already used."), frappe.DoesNotExistError)
    doc = frappe.get_doc("BusinessMember", row)
    email = doc.email
    if frappe.db.exists("User", email):
        if frappe.session.user != email:
            frappe.throw(_("Sign in with {0} first, then open the invitation again.").format(email), frappe.PermissionError)
        user = email
    else:
        name = (full_name or doc.full_name or "").strip()
        if not name or not password:
            frappe.throw(_("Write your name and choose a password."))
        from book_marketplace.auth.api.auth import _setup_new_account, ensure_role
        ensure_role("Customer")
        u = frappe.get_doc({"doctype": "User", "email": email, "first_name": name, "new_password": password,
                            "roles": [{"role": "Website User"}, {"role": "Customer"}]})
        u.insert(ignore_permissions=True)
        _setup_new_account(u.name, name, email, auto_business=False)
        user = u.name
    doc.user = user
    doc.status = "Active"
    doc.is_active = 1
    doc.invite_token = ""
    doc.accepted_on = now_datetime()
    doc.save(ignore_permissions=True)
    return {"business": doc.business_profile, "role": doc.role, "user": user}
