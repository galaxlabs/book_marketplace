import frappe


def after_install():
    create_default_roles()


def create_default_roles():
    roles = ["Publisher", "Bookseller", "Distributor", "Agent"]
    for role in roles:
        if not frappe.db.exists("Role", role):
            doc = frappe.get_doc({"doctype": "Role", "role_name": role})
            doc.insert(ignore_permissions=True)
