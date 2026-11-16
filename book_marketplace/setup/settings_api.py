import frappe
from frappe import _

from book_marketplace.auth.doctype.marketplace_settings.marketplace_settings import PROFILE_TYPE_SETTING

HELP = {
    "Publisher": {
        "en": "Lets a user publish books, set MRP and manage their own catalog. Keep off if the app should not accept publishers.",
        "ur": "ناشر کتابیں شائع کرنے، ایم آر پی طے کرنے اور اپنا کیٹلاگ چلانے کی سہولت پاتا ہے۔ اگر ایپ میں ناشر نہیں چاہیے تو بند رکھیں۔",
    },
    "Bookseller": {
        "en": "Lets a user run a shop, buy from publishers and distributors and sell to customers. Keep off if no shops should register.",
        "ur": "کتب فروش دکان چلاتا ہے، ناشرین اور تقسیم کاروں سے خریداری کرتا ہے اور گاہکوں کو فروخت کرتا ہے۔ بند رکھیں اگر دکانیں رجسٹر نہیں ہونی چاہئیں۔",
    },
    "Distributor": {
        "en": "Lets a user move stock from publishers to booksellers and manage branches and transfers. Keep off if no distributors should register.",
        "ur": "تقسیم کار ناشر سے کتب فروش تک اسٹاک پہنچاتا ہے اور شاخوں و ٹرانسفر کا انتظام کرتا ہے۔ بند رکھیں اگر تقسیم کار رجسٹر نہیں ہونے چاہئیں۔",
    },
}


@frappe.whitelist(allow_guest=True)
def get_business_types():
    settings = frappe.get_single("Marketplace Settings")
    return [
        {
            "profile_type": ptype,
            "enabled": bool(settings.get(field)),
            "help_en": HELP[ptype]["en"],
            "help_ur": HELP[ptype]["ur"],
        }
        for ptype, field in PROFILE_TYPE_SETTING.items()
    ]


@frappe.whitelist()
def set_business_type(profile_type, enabled):
    if "System Manager" not in frappe.get_roles():
        frappe.throw(_("Only an administrator can change business types."), frappe.PermissionError)
    if profile_type not in PROFILE_TYPE_SETTING:
        frappe.throw(_("Unknown business type."))
    settings = frappe.get_single("Marketplace Settings")
    settings.set(PROFILE_TYPE_SETTING[profile_type], 1 if int(enabled or 0) else 0)
    settings.save(ignore_permissions=True)
    return {"profile_type": profile_type, "enabled": bool(settings.get(PROFILE_TYPE_SETTING[profile_type]))}
