import frappe
from frappe import _
from frappe.model.document import Document

from book_marketplace.auth.permissions import ROLES, codes


class BusinessMember(Document):
    """A person who works for a business and may use it within the limits of a role.
    Status Invited: the owner sent an invitation (invite_token); Active: the person joined; Disabled: switched off."""

    def validate(self):
        if self.role not in ROLES:
            frappe.throw(_("Choose a role: {0}").format(", ".join(ROLES)))
        if not self.user and not self.email:
            frappe.throw(_("A member needs a user or an email."))
        owner = frappe.db.get_value("BusinessProfile", self.business_profile, "user")
        who = self.user or self.email
        if who and who.lower() == (owner or "").lower():
            frappe.throw(_("The owner is already in charge of this business."))
        for field in ("extra_permissions", "removed_permissions"):
            text = (self.get(field) or "").replace(",", " ").split()
            bad = [c for c in text if c not in codes(" ".join(text))]
            if bad:
                frappe.throw(_("Unknown permission: {0}").format(", ".join(bad)))
        flt = {"business_profile": self.business_profile, "name": ["!=", self.name or ""]}
        if self.user and frappe.db.exists("BusinessMember", {**flt, "user": self.user}):
            frappe.throw(_("This person is already in the team."))
        if self.email and frappe.db.exists("BusinessMember", {**flt, "email": self.email}):
            frappe.throw(_("This email is already invited."))
        if self.user and not self.is_active and self.status == "Active":
            self.status = "Disabled"
