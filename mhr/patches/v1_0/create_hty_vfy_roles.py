# MI1-I61 (Raj 2026-06-27): create the 'HTY User' and 'VFY User' roles
# used by mhr.utilis.enforce_role_scoped_transaction_type so admins can
# assign users a mode and force every mhr report to that mode
# regardless of what the user picks in the report's Transaction Type
# filter.
#
# Idempotent — skips if the role already exists.

import frappe


ROLES = ("HTY User", "VFY User")


def execute():
    for name in ROLES:
        if frappe.db.exists("Role", name):
            continue
        role = frappe.new_doc("Role")
        role.role_name = name
        role.desk_access = 1
        role.insert(ignore_permissions=True)
    frappe.db.commit()
