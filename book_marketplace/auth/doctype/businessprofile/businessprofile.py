import secrets

import frappe
from frappe import _
from frappe.model.document import Document

from book_marketplace.auth.doctype.marketplace_settings.marketplace_settings import is_profile_type_enabled


class BusinessProfile(Document):
    def validate(self):
        self.validate_enabled_type()
        self.validate_one_profile_per_type()
        if not self.publisher_code:
            self.publisher_code = new_publisher_code()

    def before_save(self):
        if self.status == "Approved" and not self.approved_on:
            self.approved_on = frappe.utils.today()

    def after_insert(self):
        ensure_publisher_store(self)

    def on_update(self):
        ensure_publisher_store(self)

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


PUBLISHER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, so it reads clearly


def new_publisher_code():
    """A random 6-character code that no other profile has."""
    while True:
        code = "".join(secrets.choice(PUBLISHER_CODE_ALPHABET) for _ in range(6))
        if not frappe.db.exists("BusinessProfile", {"publisher_code": code}):
            return code


def ensure_publisher_store(profile):
    """Every publisher gets a store automatically. It is published once the profile is Approved."""
    if profile.profile_type != "Publisher":
        return
    approved = profile.status == "Approved"
    store_name = frappe.db.get_value("BusinessStore", {"business_profile": profile.name}, "name")
    if not store_name:
        slug = profile.name.lower()
        if frappe.db.exists("BusinessStore", {"slug": slug}):
            slug = f"{slug}-{profile.name[-6:].lower()}"
        frappe.get_doc({
            "doctype": "BusinessStore",
            "business_profile": profile.name,
            "store_name": profile.business_name or profile.name,
            "slug": slug,
            "address": profile.address,
            "city": profile.city,
            "contact_phone": profile.phone,
            "contact_email": profile.email,
            "is_published": 1 if approved else 0,
        }).insert(ignore_permissions=True)
    elif approved:
        frappe.db.set_value("BusinessStore", store_name, "is_published", 1)
