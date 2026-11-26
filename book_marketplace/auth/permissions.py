"""Who may do what inside a business (company).

The owner (BusinessProfile.user) and a System Manager may do everything. A team member (BusinessMember) may do what the
role gives, plus what the owner added for that person, minus what the owner took away. Anything not listed is refused.
Code that has not been moved to these checks still asks for the owner (_assert_profile_owner), so a member gets nothing there."""
import frappe
from frappe import _

# code -> (Urdu, English)
PERMISSIONS = {
    "catalog.view": ("کیٹلاگ دیکھنا", "View the catalog"),
    "catalog.edit": ("کیٹلاگ میں کتابیں بدلنا", "Edit the catalog"),
    "prices.edit": ("قیمتیں بدلنا", "Change prices"),
    "stock.view": ("اسٹاک اور ذخیرہ گاہیں دیکھنا", "View stock and storage places"),
    "stock.edit": ("اسٹاک کی مقدار اور منتقلی", "Change stock and move it between places"),
    "locations.manage": ("ذخیرہ گاہیں بنانا اور بند کرنا", "Create and switch off storage places"),
    "orders.view": ("آرڈر دیکھنا", "View orders"),
    "orders.manage": ("آرڈر چلانا", "Handle orders"),
    "purchases.view": ("خریداری دیکھنا", "View purchases"),
    "purchases.manage": ("خریداری کرنا اور وصول کرنا", "Make and receive purchases"),
    "sales.view": ("فروخت دیکھنا", "View sales"),
    "sales.create": ("بل اور فروخت بنانا", "Make bills and sales"),
    "finance.view": ("مالی رپورٹیں دیکھنا", "View money reports"),
    "finance.manage": ("مالی لین دین اور کھاتے", "Manage money and accounts"),
    "reports.view": ("رپورٹیں دیکھنا", "View reports"),
    "team.manage": ("ٹیم اور اجازتیں", "Manage the team and permissions"),
    "settings.manage": ("کاروبار کی ترتیبات", "Change business settings"),
}

_ALL = set(PERMISSIONS)
_VIEW = {c for c in PERMISSIONS if c.endswith(".view")}

# role -> (Urdu, English, permissions)
ROLES = {
    "Manager": ("مینیجر", "Manager", _ALL - {"team.manage", "finance.manage", "settings.manage"}),
    "Storekeeper": ("اسٹور کیپر", "Storekeeper", {"stock.view", "stock.edit", "locations.manage", "catalog.view", "orders.view", "purchases.view"}),
    "Cashier": ("کیشیئر", "Cashier", {"sales.create", "sales.view", "catalog.view", "stock.view", "orders.view"}),
    "Viewer": ("صرف دیکھنے والا", "Viewer", _VIEW),
}


def codes(text):
    """The permission codes written in a text (comma or space separated); unknown ones are dropped."""
    return {c for c in (text or "").replace(",", " ").split() if c in PERMISSIONS}


def member_permissions(role, extra="", removed=""):
    base = set(ROLES[role][2]) if role in ROLES else set()
    return (base | codes(extra)) - codes(removed)


def _is_system_manager(user):
    return "System Manager" in frappe.get_roles(user)


def membership(profile_id, user=None):
    """The active membership row of this user in this business, or None."""
    user = user or frappe.session.user
    return frappe.db.get_value(
        "BusinessMember", {"business_profile": profile_id, "user": user, "is_active": 1, "status": "Active"},
        ["name", "role", "extra_permissions", "removed_permissions"], as_dict=True)


def permissions_of(profile_id, user=None):
    """{"*"} for the owner and for a System Manager; else the member's permissions; else an empty set."""
    user = user or frappe.session.user
    if not user or user == "Guest":
        return set()
    if _is_system_manager(user):
        return {"*"}
    prof = frappe.db.get_value("BusinessProfile", profile_id, ["user", "status"], as_dict=True)
    if not prof:
        return set()
    if prof.user == user:
        return {"*"}
    if prof.status != "Approved":
        return set()
    row = membership(profile_id, user)
    return member_permissions(row.role, row.extra_permissions, row.removed_permissions) if row else set()


def has_access(profile_id, permission, user=None):
    perms = permissions_of(profile_id, user)
    return "*" in perms or permission in perms


def assert_access(profile_id, permission):
    if permission not in PERMISSIONS:
        frappe.throw(f"Unknown permission {permission}")
    if not has_access(profile_id, permission):
        frappe.throw(_("Not permitted for this business profile."), frappe.PermissionError)
