import frappe
from frappe import _
from frappe.model.document import Document

from book_marketplace.auth.doctype.marketplace_settings.marketplace_settings import is_profile_type_enabled


class BusinessProfile(Document):
    def validate(self):
        self.validate_enabled_type()
        self.validate_one_profile_per_type()

    def before_save(self):
        if self.status == "Approved" and not self.approved_on:
            self.approved_on = frappe.utils.today()

    def validate_enabled_type(self):
        if self.is_new() and not frappe.flags.in_import and not is_profile_type_enabled(self.profile_type):
            frappe.throw(_("{0} is switched off in Marketplace Settings.").format(self.profile_type))

    def validate_one_profile_per_type(self):
        if not self.user or not self.profile_type:
            return
        duplicate = frappe.db.get_value(
            "BusinessProfile",
            {"user": self.user, "profile_type": self.profile_type, "name": ["!=", self.name or ""]},
            "name",
        )
        if duplicate:
            frappe.throw(_("This user already has a {0} profile ({1}).").format(self.profile_type, duplicate))
