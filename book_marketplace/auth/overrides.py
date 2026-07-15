import frappe
from frappe import _
from frappe.auth import LoginManager


class BookMarketplaceLoginManager(LoginManager):
    def validate_for_enabled_user(self):
        super().validate_for_enabled_user()
        if self.user == "Administrator":
            return
        profiles = frappe.get_all(
            "BusinessProfile",
            filters={"user": self.user, "status": "Approved"},
            limit=1,
        )
        if not profiles:
            pending = frappe.get_all(
                "BusinessProfile",
                filters={"user": self.user},
                fields=["status"],
                limit=1,
            )
            if pending:
                if pending[0].status == "Pending":
                    frappe.throw(_("Your account is pending approval."))
                elif pending[0].status == "Rejected":
                    frappe.throw(_("Your account has been rejected."))
            else:
                frappe.throw(_("No business profile found. Please sign up first."))

    def has_rejected_profile(self):
        return frappe.db.exists(
            "BusinessProfile",
            {"user": self.user, "status": "Rejected"},
        )

    def has_pending_profile(self):
        return frappe.db.exists(
            "BusinessProfile",
            {"user": self.user, "status": "Pending"},
        )


def login(doc, method=None):
    user = frappe.session.user
    if user == "Administrator":
        return
    profiles = frappe.get_all(
        "BusinessProfile",
        filters={"user": user, "status": "Approved"},
        fields=["name", "profile_type", "business_name"],
    )
    if not profiles or len(profiles) == 0:
        frappe.local.response["message"] = {
            "user": user,
            "profiles": [],
            "pending": True,
        }
        return
    frappe.local.response["message"] = {
        "user": user,
        "profiles": profiles,
        "pending": False,
    }
