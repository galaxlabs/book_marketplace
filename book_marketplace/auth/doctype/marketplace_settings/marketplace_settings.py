import frappe
from frappe.model.document import Document

# Profile type -> settings field that switches it on or off.
PROFILE_TYPE_SETTING = {
    "Publisher": "enable_publisher",
    "Bookseller": "enable_bookseller",
    "Distributor": "enable_distributor",
}


class MarketplaceSettings(Document):
    pass


def enabled_profile_types():
    """Profile types the admin has switched on. Agent is not gated here and stays available."""
    settings = frappe.get_single("Marketplace Settings")
    types = [ptype for ptype, field in PROFILE_TYPE_SETTING.items() if settings.get(field)]
    types.append("Agent")
    return types


def is_profile_type_enabled(profile_type):
    field = PROFILE_TYPE_SETTING.get(profile_type)
    if not field:
        return True
    return bool(frappe.db.get_single_value("Marketplace Settings", field))
